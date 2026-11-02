"""语料入库：文件发现 → 清洗 → 切章 → 切片 → Embedding → Qdrant + MySQL 镜像。

幂等策略：
- 文件级：content_hash 未变且已有切片 → 跳过（reused）。
- 切片级：(book_id, chunk_no) 唯一；内容不变的切片复用原 qdrant_point_id，只增量 upsert 新/变更切片。
- 重复执行后 Qdrant 点数不增长。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.models.book import Book, Chapter, Chunk
from app.models.ops import BootstrapRun, IngestTask
from app.services.llm.embeddings import embed_texts
from app.services.rag import vector_store
from app.utils.text import (
    chunk_chapter,
    estimate_tokens,
    normalize_text,
    sha256_text,
    short_hash,
    split_chapters,
)
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

TEXT_SUFFIXES = {".txt", ".md"}
# 语料目录里的说明文件不是书
_SKIP_NAMES = {"readme.md", "readme.txt", "readme", "说明.md", "说明.txt", "license", "license.md"}
_TITLE_AUTHOR = re.compile(r"^(?P<title>.+?)\s*(?:-{1,2}|—|_)\s*(?P<author>[^-—_]+)$")
_CHARS_PER_MINUTE = 400  # 中文默读速度，用于估算阅读时长


@dataclass
class IngestReport:
    task_id: int | None = None
    book_id: int = 0
    book_title: str = ""
    source_file: str = ""
    status: str = "done"
    reusable: bool = False
    chapters: int = 0
    chunks_total: int = 0
    chunks_new: int = 0
    chunks_reused: int = 0
    points_upserted: int = 0
    char_count: int = 0
    est_minutes: int = 0
    elapsed_ms: int = 0
    stages: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "source_file": self.source_file,
            "status": self.status,
            "reusable": self.reusable,
            "chapters": self.chapters,
            "chunks_total": self.chunks_total,
            "chunks_new": self.chunks_new,
            "chunks_reused": self.chunks_reused,
            "points_upserted": self.points_upserted,
            "char_count": self.char_count,
            "est_minutes": self.est_minutes,
            "elapsed_ms": self.elapsed_ms,
            "stages": self.stages,
        }


def parse_filename(path: Path) -> tuple[str, str]:
    """`书名-作者.txt` → (书名, 作者)；无法解析时作者=佚名。"""
    stem = path.stem.strip()
    match = _TITLE_AUTHOR.match(stem)
    if match:
        return match.group("title").strip(), match.group("author").strip()
    return stem, "佚名"


def discover_corpus(corpus_dir: str | None = None) -> list[Path]:
    """递归扫描语料目录，返回按文件名排序的文本文件。"""
    root = Path(corpus_dir or get_settings().corpus_dir)
    if not root.exists():
        return []
    files = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in TEXT_SUFFIXES
        and not p.name.startswith(".")
        and p.name.lower() not in _SKIP_NAMES
    ]
    return sorted(files, key=lambda p: p.as_posix())


def read_corpus(path: Path) -> str:
    settings = get_settings()
    size = path.stat().st_size
    if size > settings.max_upload_mb * 1024 * 1024 * 5:
        raise AppError(code="CORPUS_TOO_LARGE", status_code=413, message=f"语料文件过大：{path.name}")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise AppError(code="CORPUS_ENCODING", status_code=400, message=f"无法识别编码：{path.name}")
    text = normalize_text(text)
    if len(text) > settings.ingest_max_chars:
        raise AppError(code="CORPUS_TOO_LARGE", status_code=413, message="语料超过单本上限")
    return text


def _relative(path: Path) -> str:
    root = Path(get_settings().corpus_dir).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


async def _find_book(db: AsyncSession, title: str, author: str) -> Book | None:
    return await db.scalar(select(Book).where(Book.title == title, Book.author == author))


async def ensure_book_for_file(db: AsyncSession, path: Path) -> Book:
    """语料文件对应书目不存在时自动登记，保证「放文件即入库」。"""
    title, author = parse_filename(path)
    book = await _find_book(db, title, author)
    if book:
        return book
    book = Book(
        title=title[:255],
        author=author[:255],
        category="未分类",
        tags=[],
        intro=f"由语料文件自动登记：《{title}》（{author}）。请管理员补充简介与分类。",
        difficulty=3,
        total_minutes=0,
        source="corpus",
        status="published",
    )
    db.add(book)
    await db.flush()
    logger.info("book_auto_registered", extra={"extra_fields": {"book_id": book.id, "title": title}})
    return book


async def _create_task(db: AsyncSession, book: Book, source: str, key: str) -> IngestTask:
    existing = await db.scalar(
        select(IngestTask.id).where(IngestTask.book_id == book.id, IngestTask.idempotency_key == key)
    )
    task = IngestTask(
        book_id=book.id,
        source_file=source,
        idempotency_key=key if not existing else f"{key}-{short_hash(str(now_utc()), 8)}",
        status="running",
        stage="init",
        started_at=now_utc(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def ingest_file(
    db: AsyncSession,
    path: Path,
    book: Book | None = None,
    force: bool = False,
    task: IngestTask | None = None,
) -> IngestReport:
    started = datetime.now()
    source = _relative(path)
    book = book or await ensure_book_for_file(db, path)
    report = IngestReport(book_id=book.id, book_title=book.title, source_file=source)

    # ---------- 1. 读取与 hash ----------
    text = read_corpus(path)
    digest = sha256_text(text)
    existing_chunks = int(
        await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.book_id == book.id)) or 0
    )
    index_points = await vector_store.probe_collection()
    index_ok = bool(index_points.get("exists")) and int(index_points.get("points") or 0) >= existing_chunks
    if not force and existing_chunks > 0 and index_ok and book.content_hash == digest:
        report.reusable = True
        report.chunks_total = existing_chunks
        report.chunks_reused = existing_chunks
        report.char_count = int(book.char_count or len(text))
        report.status = "skipped"
        report.stages = [{"stage": "hash", "status": "unchanged", "chunks": existing_chunks,
                          "qdrant_points": index_points.get("points", 0)}]
        logger.info("ingest_skipped_unchanged", extra={"extra_fields": {"book_id": book.id}})
        return report

    task = task or await _create_task(db, book, source, digest[:32])
    report.task_id = task.id

    async def stage(name: str, **payload) -> None:
        report.stages.append({"stage": name, **payload})
        task.stage = name
        await db.commit()

    await stage("read", chars=len(text), hash=digest[:12])

    # ---------- 2. 切章 ----------
    raw_chapters = split_chapters(text)
    if not raw_chapters:
        raise AppError(code="CORPUS_EMPTY", status_code=400, message="语料内容为空或无法切分")

    # 旧切片按 (chapter_no, chunk_no) 索引，用于复用 point_id
    old_rows = (
        await db.execute(select(Chunk).where(Chunk.book_id == book.id))
    ).scalars().all()
    old_by_key = {(row.chapter_no, row.chunk_no): row for row in old_rows}

    # 章节：沿用已有章节 id，按 chapter_no 更新
    chapter_rows = (
        await db.execute(select(Chapter).where(Chapter.book_id == book.id))
    ).scalars().all()
    chapter_by_no = {row.chapter_no: row for row in chapter_rows}
    for raw in raw_chapters:
        row = chapter_by_no.get(raw.chapter_no)
        chapter_hash = sha256_text(raw.content)
        if row is None:
            row = Chapter(
                book_id=book.id,
                chapter_no=raw.chapter_no,
                title=raw.title,
                char_count=len(raw.content),
                source_file=source,
                content_hash=chapter_hash,
            )
            db.add(row)
            await db.flush()
            chapter_by_no[raw.chapter_no] = row
        else:
            row.title = raw.title
            row.char_count = len(raw.content)
            row.content_hash = chapter_hash
    await stage("split", chapters=len(raw_chapters))

    # ---------- 3. 切片 ----------
    planned: list[dict] = []
    chunk_no = 0
    for raw in raw_chapters:
        chapter = chapter_by_no[raw.chapter_no]
        for piece in chunk_chapter(raw):
            chunk_no += 1
            planned.append(
                {
                    "chapter_id": chapter.id,
                    "chapter_no": raw.chapter_no,
                    "chunk_no": chunk_no,
                    "text": piece.text,
                    "tokens": piece.token_estimate,
                    "start": piece.char_start,
                    "end": piece.char_end,
                    "hash": sha256_text(piece.text),
                }
            )
    if not planned:
        raise AppError(code="CORPUS_EMPTY", status_code=400, message="切片结果为空")
    await stage("chunk", chunks=len(planned), avg_tokens=round(sum(p["tokens"] for p in planned) / len(planned)))

    # ---------- 4. 复用判定 ----------
    to_embed: list[dict] = []
    reused = 0
    for item in planned:
        old = old_by_key.get((item["chapter_no"], item["chunk_no"]))
        if old and old.content_hash == item["hash"] and not force:
            item["point_id"] = old.qdrant_point_id
            item["chunk_id"] = old.id
            reused += 1
        else:
            item["point_id"] = vector_store.new_point_id()
            item["chunk_id"] = old.id if old else None
            to_embed.append(item)
    report.chunks_reused = reused
    report.chunks_new = len(to_embed)

    # ---------- 5. Embedding + Qdrant ----------
    if to_embed:
        embed_inputs = [f"《{book.title}》第{item['chapter_no']}章\n{item['text']}" for item in to_embed]
        vectors = await embed_texts(embed_inputs)
        points = [
            {
                "id": item["point_id"],
                "vector": vector,
                "payload": {
                    "chunk_id": item["chunk_id"] or 0,
                    "book_id": book.id,
                    "book_title": book.title,
                    "author": book.author,
                    "chapter_id": item["chapter_id"],
                    "chapter_no": item["chapter_no"],
                    "chunk_no": item["chunk_no"],
                    "token_count": item["tokens"],
                    "content_hash": item["hash"],
                    "text": item["text"][:1500],
                },
            }
            for item, vector in zip(to_embed, vectors, strict=True)
        ]
        written = await vector_store.upsert_chunks(points, get_settings().task_batch_size)
        report.points_upserted = written
    await stage("embed", embedded=len(to_embed), upserted=report.points_upserted)

    # ---------- 6. MySQL 镜像 ----------
    keep_point_ids: set[str] = set()
    for item in planned:
        keep_point_ids.add(item["point_id"])
        if item["chunk_id"] is not None:
            row = await db.get(Chunk, item["chunk_id"])
            if row is None:
                continue
            row.content = item["text"]
            row.token_count = item["tokens"]
            row.char_start, row.char_end = item["start"], item["end"]
            row.chapter_id = item["chapter_id"]
            row.chapter_no = item["chapter_no"]
            row.content_hash = item["hash"]
            row.qdrant_point_id = item["point_id"]
            row.embedding_status = "done"
        else:
            db.add(
                Chunk(
                    book_id=book.id,
                    chapter_id=item["chapter_id"],
                    chapter_no=item["chapter_no"],
                    chunk_no=item["chunk_no"],
                    content=item["text"],
                    token_count=item["tokens"],
                    char_start=item["start"],
                    char_end=item["end"],
                    qdrant_point_id=item["point_id"],
                    content_hash=item["hash"],
                )
            )
    # 删除已不存在的旧切片（书稿变短）及其向量点
    stale = [row for key, row in old_by_key.items() if row.qdrant_point_id not in keep_point_ids]
    if stale:
        await vector_store.delete_chunks([row.qdrant_point_id for row in stale])
        for row in stale:
            await db.delete(row)
    await db.flush()

    # ---------- 7. 书目元信息 ----------
    char_count = sum(len(raw.content) for raw in raw_chapters)
    book.char_count = char_count
    book.chapter_count = len(raw_chapters)
    book.total_minutes = max(int(char_count / _CHARS_PER_MINUTE), 20)
    book.content_hash = digest
    book.ingested_at = now_utc()
    book.source = book.source or "corpus"
    report.chapters = len(raw_chapters)
    report.chunks_total = len(planned)
    report.char_count = char_count
    report.est_minutes = book.total_minutes

    task.status = "done"
    task.stage = "done"
    task.progress = 100
    task.total_chunks = len(planned)
    task.done_chunks = len(planned)
    task.finished_at = now_utc()
    report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
    task.report = report.as_dict()
    await db.commit()
    logger.info(
        "ingest_done",
        extra={"extra_fields": {
            "book_id": book.id,
            "chunks": len(planned),
            "new": report.chunks_new,
            "reused": reused,
            "ms": report.elapsed_ms,
        }},
    )
    return report


async def ingest_book_id(
    db: AsyncSession,
    book_id: int,
    file_path: str | None = None,
    force: bool = False,
    corpus_dir: str | None = None,
) -> IngestReport:
    book = await db.get(Book, book_id)
    if book is None:
        raise not_found("书籍不存在")
    if file_path:
        path = Path(file_path)
    else:
        matches = [
            p
            for p in discover_corpus(corpus_dir)
            if parse_filename(p) == (book.title, book.author)
        ]
        if not matches:
            raise not_found(f"未找到《{book.title}》的语料文件，请放入 {get_settings().corpus_dir}")
        path = matches[0]
    if not path.exists():
        raise not_found(f"语料文件不存在：{path}")
    return await ingest_file(db, path, book=book, force=force)


async def scan_and_ingest(
    db: AsyncSession,
    corpus_dir: str | None = None,
    limit: int | None = None,
    trigger: str = "scan",
) -> list[IngestReport]:
    """全量扫描：新增文件入库、变更文件重新入库、未变化跳过。"""
    reports: list[IngestReport] = []
    files = discover_corpus(corpus_dir)
    run: BootstrapRun | None = None
    if trigger == "bootstrap":
        run = BootstrapRun(trigger=trigger, status="running")
        db.add(run)
        await db.commit()
    for path in files[: limit or len(files)]:
        # 大文件的 Embedding 可能耗时数分钟：释放长事务
        await db.commit()
        try:
            report = await ingest_file(db, path)
            reports.append(report)
        except AppError as exc:
            logger.error(
                "ingest_failed",
                extra={"extra_fields": {"file": path.name, "code": exc.code, "message": exc.message}},
            )
            await db.rollback()
            failed = IngestReport(book_id=0, source_file=_relative(path), status="failed")
            failed.stages = [{"stage": "error", "code": exc.code, "message": exc.message}]
            reports.append(failed)
    if run is not None:
        run.status = "done" if all(r.status != "failed" for r in reports) else "partial"
        run.books_ingested = sum(1 for r in reports if r.status == "done")
        run.chunks_created = sum(r.chunks_new for r in reports)
        run.stages = [r.as_dict() for r in reports]
        run.finished_at = now_utc()
        await db.commit()
    return reports


def estimate_reading_minutes(chars: int) -> int:
    return max(int(chars / _CHARS_PER_MINUTE), 1)


def chunk_token_estimate(text: str) -> int:
    return estimate_tokens(text)
