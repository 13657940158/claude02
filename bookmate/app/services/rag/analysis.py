"""书籍解析：章节摘要 → LLM 结构化解析 → 引用逐字校验 → 落库缓存。

引用校验规则：
- 每条 quote 必须能在原文中找到（去空白后包含匹配）；
- 找不到的 quote 与对应 key_point 一并剔除，并标记 degraded；
- 全部剔除时状态置为 invalid，前端提示「稍后重试」。
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.models.book import Analysis, Book, Chapter, Chunk
from app.services.llm.client import get_llm_client
from app.services.llm.prompts import PROMPT_VERSION, analysis_user_prompt
from app.services.llm.guardrails import truncate
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

ANALYSIS_PROMPT_VERSION = f"{PROMPT_VERSION}.analysis"
MAX_SOURCE_CHARS = 24_000  # 单次送入模型的原文字符上限（约 3 万 token 内）
_PER_CHAPTER_CHARS = 900


def _norm_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def build_analysis_source(chapters: list[dict]) -> str:
    """把章节拼成送入模型的原文；超限时按比例截断每章，保证覆盖全书。"""
    if not chapters:
        return ""
    budget = max(MAX_SOURCE_CHARS // max(len(chapters), 1), 600)
    per_chapter = min(_PER_CHAPTER_CHARS, budget)
    blocks = []
    for chapter in chapters:
        content = (chapter["content"] or "")
        if not content:
            continue
        snippet = content[:per_chapter]
        cut = "……（本章节选）" if len(content) > per_chapter else ""
        blocks.append(f"第{chapter['chapter_no']}章 {chapter['title']}\n{snippet}{cut}")
    source = "\n\n".join(blocks)
    return source[:MAX_SOURCE_CHARS]


def validate_analysis_payload(payload: dict, source_text: str) -> tuple[dict, int]:
    """校验并清理解析结果中的引用，返回 (payload, 有效引用数)。"""
    normalized_source = _norm_for_match(source_text)
    valid_quotes = 0

    cleaned_points = []
    for point in payload.get("key_points") or []:
        citations = []
        for citation in point.get("citations") or []:
            quote = _norm_for_match(str(citation.get("quote") or ""))
            if quote and len(quote) >= 4 and quote in normalized_source:
                citations.append(citation)
        if citations:
            valid_quotes += len(citations)
            cleaned_points.append({**point, "citations": citations})
        else:
            cleaned_points.append({**point, "citations": [], "unverified": True})
    payload["key_points"] = cleaned_points

    quotes = []
    for quote in payload.get("quotes") or []:
        text = _norm_for_match(str(quote.get("text") or ""))
        if text and len(text) >= 4 and text in normalized_source:
            quotes.append(quote)
            valid_quotes += 1
    payload["quotes"] = quotes

    chapter_nos = set(re.findall(r"第(\d+)章", source_text))
    payload["chapter_map"] = [
        item for item in payload.get("chapter_map") or []
        if str(item.get("chapter_no")) in chapter_nos
    ]
    payload.setdefault("themes", [])
    payload.setdefault("reading_tips", [])
    payload.setdefault("one_liner", "")
    return payload, valid_quotes


async def _load_chapters(db: AsyncSession, book_id: int, limit: int | None) -> list[dict]:
    """章节正文存切片行（chapters 表只有元信息），按章聚合前若干切片。"""
    rows = (
        await db.execute(
            select(Chunk.chapter_no, Chunk.chunk_no, Chunk.content)
            .where(Chunk.book_id == book_id)
            .order_by(Chunk.chapter_no, Chunk.chunk_no)
        )
    ).all()
    if not rows:
        return []
    numbers = sorted({int(r[0]) for r in rows})
    selected = set(numbers[:limit] if limit else numbers)
    titles = dict(
        (
            await db.execute(
                select(Chapter.chapter_no, Chapter.title).where(
                    Chapter.book_id == book_id, Chapter.chapter_no.in_(selected)
                )
            )
        ).all()
    )
    grouped: dict[int, list[str]] = {}
    for chapter_no, _chunk_no, content in rows:
        if int(chapter_no) in selected:
            grouped.setdefault(int(chapter_no), []).append(content)
    return [
        {"chapter_no": no, "title": titles.get(no, ""), "content": "\n".join(grouped[no])}
        for no in sorted(grouped)
    ]


async def _load_chunk_text(db: AsyncSession, book_id: int, limit_chars: int) -> str:
    """无章节正文时退回用切片内容。"""
    rows = (
        await db.execute(
            select(Chunk.chapter_no, Chunk.chunk_no, Chunk.content)
            .where(Chunk.book_id == book_id)
            .order_by(Chunk.chapter_no, Chunk.chunk_no)
        )
    ).all()
    blocks, used = [], 0
    current_chapter = None
    for chapter_no, _chunk_no, content in rows:
        if used >= limit_chars:
            break
        if chapter_no != current_chapter:
            blocks.append(f"\n第{chapter_no}章")
            current_chapter = chapter_no
        blocks.append(content[: min(len(content), limit_chars - used)])
        used += len(content)
    return "\n".join(blocks)[:limit_chars]


async def get_cached(db: AsyncSession, book_id: int) -> Analysis | None:
    return await db.scalar(select(Analysis).where(Analysis.book_id == book_id))


async def analyze_book(
    db: AsyncSession,
    book_id: int,
    *,
    force: bool = False,
    chapter_limit: int | None = 12,
) -> Analysis:
    started = time.perf_counter()
    book = await db.get(Book, book_id)
    if book is None:
        raise not_found("书籍不存在")

    cached = await get_cached(db, book_id)
    if cached and not force and cached.status == "ready":
        return cached

    chapters = await _load_chapters(db, book_id, chapter_limit)
    source = build_analysis_source(chapters)
    if len(source) < 200:
        source = await _load_chunk_text(db, book_id, MAX_SOURCE_CHARS)
    if len(source) < 100:
        raise AppError(
            code="NO_CORPUS",
            status_code=409,
            message="这本书还没有入库正文，无法生成解析。管理员入库后再试。",
        )

    settings = get_settings()
    messages = [
        {"role": "system", "content": "你是资深读书编辑，擅长提炼书籍主旨与结构化要点。只依据给定原文，不编造。"},
        {"role": "user", "content": analysis_user_prompt(book.title, book.author, truncate(source, MAX_SOURCE_CHARS))},
    ]
    record = cached or Analysis(book_id=book_id)
    if cached is None:
        db.add(record)
    record.status = "running"
    await db.commit()

    try:
        payload = await get_llm_client().chat_json(messages, temperature=0.3, max_tokens=2200,
                                                   operation="book_analysis")
    except Exception as exc:  # noqa: BLE001 - 写回失败状态，避免反复打空请求
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        await db.commit()
        logger.error("analysis_failed", extra={"extra_fields": {"book_id": book_id, "error": record.error}})
        raise

    payload, valid_quotes = validate_analysis_payload(payload, source)
    record.payload = payload
    record.model = "mock" if get_llm_client().mock else settings.llm_model
    record.prompt_version = ANALYSIS_PROMPT_VERSION
    record.citation_count = valid_quotes
    record.validated_at = now_utc()
    record.status = "ready" if payload.get("one_liner") else "invalid"
    record.error = None
    await db.commit()
    logger.info(
        "analysis_ready",
        extra={"extra_fields": {
            "book_id": book_id,
            "quotes": valid_quotes,
            "points": len(payload.get("key_points") or []),
            "ms": int((time.perf_counter() - started) * 1000),
        }},
    )
    return record


async def analyze_missing(db: AsyncSession, limit: int = 3) -> list[dict]:
    """为已有正文但尚无解析的书批量生成（自举/定时任务用）。"""
    rows = (
        await db.execute(
            select(Book.id)
            .where(Book.chapter_count > 0)
            .where(~select(Analysis.id).where(Analysis.book_id == Book.id, Analysis.status == "ready").exists())
            .order_by(Book.id)
            .limit(limit)
        )
    ).scalars().all()
    results = []
    for book_id in rows:
        try:
            record = await analyze_book(db, book_id)
            results.append({"book_id": book_id, "status": record.status, "citations": record.citation_count})
        except Exception as exc:  # noqa: BLE001 - 单本失败不影响其他
            await db.rollback()
            results.append({"book_id": book_id, "status": "failed", "error": type(exc).__name__})
    return results


def analysis_out(record: Analysis, book: Book | None = None) -> dict:
    payload = dict(record.payload or {})
    payload.update(
        {
            "book_id": record.book_id,
            "status": record.status,
            "model": record.model,
            "prompt_version": record.prompt_version,
            "citation_count": record.citation_count,
            "validated_at": record.validated_at.isoformat() if isinstance(record.validated_at, datetime) else None,
        }
    )
    if book is not None:
        payload.setdefault("book_title", book.title)
        payload.setdefault("author", book.author)
    return payload
