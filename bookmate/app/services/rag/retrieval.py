"""混合召回 → RRF 融合 → 在线重排 → 引用组装。

链路：
1. dense：Qdrant 余弦检索（可加 book_id / chapter_no 过滤）
2. sparse：MySQL FULLTEXT(ngram) 布尔检索，按相关度打分
3. RRF：score = Σ 1/(k + rank)，k=60
4. rerank：在线 bge-reranker；失败或未开启时保持 RRF 顺序
5. 引用：补书名/章节标题/片段，供回答与前端展示
"""
from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import RAG_RETRIEVALS
from app.services.llm.embeddings import embed_one
from app.services.llm.rerank import rerank_documents
from app.services.rag import vector_store
from app.utils.text import excerpt

logger = get_logger(__name__)
RRF_K = 60
_FTS_STOP = {"的", "了", "是", "在", "和", "与", "及", "我", "你", "他", "她", "它", "这", "那", "吗", "呢", "怎么", "如何"}
_FTS_BIGRAM_STOP = {
    "什么", "怎么", "如何", "为何", "为什", "意思", "主要", "内容", "讲了", "说的",
    "告诉", "一下", "可以", "请问", "我们", "你们", "他们", "这个", "那个", "哪些", "有什",
    "是什", "么意", "是怎", "怎样", "么样", "么要", "有哪", "些人", "书中", "作者",
}
_QUESTION_NOISE = re.compile(
    r"(是什么意思|讲了什么|主要讲什么|主要内容|告诉了我什么|告诉我们什么|"
    r"是讲什么的|讲的是什么|有什么启示|想表达什么|说了什么|是什么|为什么|怎么样|如何)"
)


@dataclass
class RetrievedChunk:
    chunk_id: int
    book_id: int
    book_title: str
    author: str
    chapter_no: int
    chapter_title: str
    chunk_no: int
    content: str
    dense_score: float = 0.0
    sparse_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: float | None = None
    citation_index: int = 0

    def as_citation(self) -> dict:
        return {
            "index": self.citation_index,
            "chunk_id": self.chunk_id,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "snippet": excerpt(self.content, limit=140),
            "score": round(self.rerank_score if self.rerank_score is not None else self.rrf_score, 4),
        }

    def context_text(self) -> str:
        return (
            f"《{self.book_title}》第{self.chapter_no}章 {self.chapter_title}："
            f"{self.content[:1200]}"
        )


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    dense_count: int = 0
    sparse_count: int = 0
    reranked: bool = False
    degraded: bool = False
    elapsed_ms: int = 0

    def contexts(self) -> list[str]:
        return [chunk.context_text() for chunk in self.chunks]

    def citations(self) -> list[dict]:
        return [chunk.as_citation() for chunk in self.chunks]


def _fts_query(query: str) -> str:
    """把自然语言问句转成 ngram 布尔检索式。

    ngram 分词器按 2 字切词，整句直接检索会因 AND 组合过严而 0 命中，
    因此先去掉疑问套话，再把中文拆成二字词，用 OR 召回并由相关度排序。
    """
    cleaned = _QUESTION_NOISE.sub(" ", query or "")
    cleaned = re.sub(r"[\s，。！？、；：,.!?;:（）()“”\"'《》]+", " ", cleaned)
    tokens: list[str] = []
    for run in re.findall(r"[一-鿿]{2,}|[A-Za-z0-9]{2,}", cleaned):
        if re.fullmatch(r"[A-Za-z0-9]+", run):
            tokens.append(run.lower())
            continue
        if len(run) == 2:
            if run not in _FTS_BIGRAM_STOP and run not in _FTS_STOP:
                tokens.append(run)
            continue
        # 长词拆成重叠二字词；保留一次完整短语提高精确命中排序
        if len(run) >= 4:
            tokens.append(run)
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _FTS_BIGRAM_STOP and bigram not in _FTS_STOP:
                tokens.append(bigram)
    seen: set[str] = set()
    uniq = [t for t in tokens if not (t in seen or seen.add(t))]
    return " ".join(uniq[:20])[:300]


async def sparse_search(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
) -> list[dict]:
    expr = _fts_query(query)
    if not expr:
        return []
    where = ["MATCH(c.content) AGAINST(:expr IN BOOLEAN MODE)"]
    params: dict = {"expr": expr, "limit": limit}
    if book_ids:
        placeholders = ",".join(f":b{i}" for i in range(len(book_ids)))
        where.append(f"c.book_id IN ({placeholders})")
        params.update({f"b{i}": bid for i, bid in enumerate(book_ids)})
    if chapter_max is not None:
        where.append("c.chapter_no <= :chapter_max")
        params["chapter_max"] = chapter_max
    sql = text(
        "SELECT c.id, c.book_id, c.chapter_no, c.chunk_no, c.content, "
        "MATCH(c.content) AGAINST(:expr IN BOOLEAN MODE) AS score "
        f"FROM chunks c WHERE {' AND '.join(where)} "
        "ORDER BY score DESC LIMIT :limit"
    )
    try:
        rows = (await db.execute(sql, params)).all()
    except Exception as exc:  # noqa: BLE001 - 全文索引缺失时降级为仅稠密召回
        logger.warning("sparse_search_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        return []
    return [
        {
            "chunk_id": int(row[0]),
            "book_id": int(row[1]),
            "chapter_no": int(row[2]),
            "chunk_no": int(row[3]),
            "content": row[4],
            "score": float(row[5] or 0),
        }
        for row in rows
    ]


async def _load_chunk_meta(db: AsyncSession, chunk_ids: list[int]) -> dict[int, dict]:
    if not chunk_ids:
        return {}
    placeholders = ",".join(f":c{i}" for i in range(len(chunk_ids)))
    params = {f"c{i}": cid for i, cid in enumerate(chunk_ids)}
    sql = text(
        "SELECT c.id, c.book_id, c.chapter_no, c.chunk_no, c.content, b.title, b.author, "
        "COALESCE(ch.title, '') AS chapter_title "
        "FROM chunks c JOIN books b ON b.id = c.book_id "
        "LEFT JOIN chapters ch ON ch.id = c.chapter_id "
        f"WHERE c.id IN ({placeholders})"
    )
    rows = (await db.execute(sql, params)).all()
    return {
        int(row[0]): {
            "chunk_id": int(row[0]),
            "book_id": int(row[1]),
            "chapter_no": int(row[2]),
            "chunk_no": int(row[3]),
            "content": row[4],
            "book_title": row[5],
            "author": row[6],
            "chapter_title": row[7] or "",
        }
        for row in rows
    }


def rrf_fuse(dense: list[dict], sparse: list[dict], k: int = RRF_K) -> list[dict]:
    scores: dict[int, dict] = {}
    for rank, item in enumerate(dense, start=1):
        entry = scores.setdefault(item["chunk_id"], {"dense": 0.0, "sparse": 0.0, "rrf": 0.0, "item": item})
        entry["dense"] = float(item.get("score", 0.0))
        entry["rrf"] += 1.0 / (k + rank)
    for rank, item in enumerate(sparse, start=1):
        entry = scores.setdefault(item["chunk_id"], {"dense": 0.0, "sparse": 0.0, "rrf": 0.0, "item": item})
        entry["sparse"] = float(item.get("score", 0.0))
        entry["rrf"] += 1.0 / (k + rank)
        if not entry["item"].get("content") and item.get("content"):
            entry["item"] = {**entry["item"], "content": item["content"]}
    merged = sorted(scores.values(), key=lambda item: item["rrf"], reverse=True)
    return [
        {
            **entry["item"],
            "dense_score": entry["dense"],
            "sparse_score": entry["sparse"],
            "rrf_score": entry["rrf"],
        }
        for entry in merged
    ]


async def retrieve(
    db: AsyncSession,
    query: str,
    *,
    top_k: int = 20,
    rerank_top_n: int | None = None,
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
    use_rerank: bool | None = None,
) -> RetrievalResult:
    settings = get_settings()
    started = time.perf_counter()
    result = RetrievalResult(query=query)
    query = (query or "").strip()[:500]
    if not query:
        RAG_RETRIEVALS.labels(stage="retrieve", status="empty").inc()
        return result

    # 1) 稠密
    dense_raw: list[dict] = []
    try:
        vector = await embed_one(query)
        dense_raw = await vector_store.search_chunks(
            vector, limit=top_k, book_ids=book_ids, chapter_max=chapter_max
        )
    except Exception as exc:  # noqa: BLE001 - 向量侧失败仍可走稀疏
        result.degraded = True
        logger.warning("dense_search_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    dense = [
        {"chunk_id": int(hit["payload"].get("chunk_id") or 0), "score": hit["score"], "content": ""}
        for hit in dense_raw
        if hit["payload"].get("chunk_id")
    ]
    if not dense:
        # Qdrant payload 未存 chunk_id 时按 (book_id, chunk_no) 回查 MySQL
        keys = [(int(h["payload"].get("book_id", 0)), int(h["payload"].get("chunk_no", 0))) for h in dense_raw]
        if keys:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, book_id, chunk_no FROM chunks WHERE "
                        + " OR ".join(f"(book_id=:b{i} AND chunk_no=:n{i})" for i in range(len(keys)))
                    ),
                    {f"{k}{i}": v for i, (b, n) in enumerate(keys) for k, v in (("b", b), ("n", n))},
                )
            ).all()
            lookup = {(int(r[1]), int(r[2])): int(r[0]) for r in rows}
            dense = [
                {"chunk_id": lookup[key], "score": hit["score"], "content": ""}
                for hit, key in zip(dense_raw, keys, strict=True)
                if key in lookup
            ]

    # 2) 稀疏
    sparse = await sparse_search(db, query, limit=top_k, book_ids=book_ids, chapter_max=chapter_max)
    result.dense_count = len(dense)
    result.sparse_count = len(sparse)
    RAG_RETRIEVALS.labels(stage="dense", status="ok" if dense else "empty").inc()
    RAG_RETRIEVALS.labels(stage="sparse", status="ok" if sparse else "empty").inc()

    # 3) RRF
    fused = rrf_fuse(dense, sparse) if (dense or sparse) else []
    fused = fused[: max(top_k, rerank_top_n or settings.rerank_top_n)]

    # 4) 元数据
    meta = await _load_chunk_meta(db, [item["chunk_id"] for item in fused])
    chunks: list[RetrievedChunk] = []
    for item in fused:
        info = meta.get(item["chunk_id"])
        if not info:
            continue
        chunks.append(
            RetrievedChunk(
                chunk_id=info["chunk_id"],
                book_id=info["book_id"],
                book_title=info["book_title"],
                author=info["author"],
                chapter_no=info["chapter_no"],
                chapter_title=info["chapter_title"],
                chunk_no=info["chunk_no"],
                content=info["content"],
                dense_score=item["dense_score"],
                sparse_score=item["sparse_score"],
                rrf_score=item["rrf_score"],
            )
        )

    # 5) 重排
    want_rerank = settings.rerank_enabled if use_rerank is None else use_rerank
    top_n = rerank_top_n or settings.rerank_top_n
    if want_rerank and chunks:
        ranked = await rerank_documents(query, [c.content for c in chunks], top_n=top_n)
        if ranked is None:
            result.degraded = True
            RAG_RETRIEVALS.labels(stage="rerank", status="failed").inc()
            chunks = chunks[:top_n]
        else:
            result.reranked = True
            RAG_RETRIEVALS.labels(stage="rerank", status="ok").inc()
            chunks = [dataclasses.replace(chunks[item["index"]], rerank_score=item["relevance_score"])
                      for item in ranked]
    else:
        chunks = chunks[:top_n]

    for index, chunk in enumerate(chunks, start=1):
        chunk.citation_index = index
    result.chunks = chunks
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "retrieve_done",
        extra={"extra_fields": {
            "query": query[:40],
            "dense": result.dense_count,
            "sparse": result.sparse_count,
            "kept": len(chunks),
            "reranked": result.reranked,
            "degraded": result.degraded,
            "ms": result.elapsed_ms,
        }},
    )
    return result


async def retrieve_in_book(
    db: AsyncSession,
    book_id: int,
    query: str,
    *,
    chapter_max: int | None = None,
    top_k: int = 12,
    rerank_top_n: int = 6,
) -> RetrievalResult:
    return await retrieve(
        db,
        query,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        book_ids=[book_id],
        chapter_max=chapter_max,
    )


async def chapter_context(db: AsyncSession, book_id: int, chapter_from: int, chapter_to: int, limit: int = 8) -> list[str]:
    """伴读用：直接取指定章节的前若干切片作为回顾材料。"""
    rows = (
        await db.execute(
            text(
                "SELECT content FROM chunks WHERE book_id=:b AND chapter_no BETWEEN :f AND :t "
                "ORDER BY chapter_no, chunk_no LIMIT :l"
            ),
            {"b": book_id, "f": chapter_from, "t": chapter_to, "l": limit},
        )
    ).all()
    return [row[0] for row in rows]
