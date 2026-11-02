"""RAG 接口：检索、问答（普通/流式）、书籍解析、管理员入库与自举。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, AdminUser, OptionalUser
from app.core.config import get_settings
from app.core.errors import AppError, forbidden, not_found, ok
from app.models.book import Book
from app.models.ops import BootstrapRun, IngestTask
from app.services.bootstrap import run_bootstrap
from app.services.llm.guardrails import check_input
from app.services.rag.analysis import (
    ANALYSIS_PROMPT_VERSION,
    analysis_out,
    analyze_book,
    get_cached,
)
from app.services.rag.answer import answer_question, blocked_answer, stream_answer
from app.services.rag.ingest import ingest_book_id, ingest_file, scan_and_ingest
from app.services.rag.retrieval import retrieve
from app.utils.limiter import enforce
from app.utils.pdf import extract_pdf_text

router = APIRouter(prefix="/rag", tags=["检索问答"])


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    book_id: int | None = None
    book_ids: list[int] | None = Field(default=None, max_length=20)
    top_k: int = Field(default=20, ge=1, le=50)
    rerank_top_n: int = Field(default=8, ge=1, le=20)
    use_rerank: bool | None = None


class AskRequest(RetrieveRequest):
    history_summary: str = Field(default="", max_length=2000)


class AnalysisRequest(BaseModel):
    force: bool = False
    chapter_limit: int | None = Field(default=12, ge=1, le=50)


class IngestScanRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=500)
    force: bool = False


def _book_ids(body: RetrieveRequest) -> list[int] | None:
    ids = list(body.book_ids or [])
    if body.book_id:
        ids.append(body.book_id)
    return ids or None


@router.post("/retrieve")
async def retrieve_endpoint(body: RetrieveRequest, db: DB, user: OptionalUser, request: Request):
    await enforce(request, "rag_retrieve", limit=60)
    guard = check_input(body.query)
    if not guard.ok:
        return ok({"query": body.query, "blocked": True, "message": guard.reason, "chunks": []})
    result = await retrieve(
        db,
        body.query,
        top_k=body.top_k,
        rerank_top_n=body.rerank_top_n,
        book_ids=_book_ids(body),
        use_rerank=body.use_rerank,
    )
    return ok(
        {
            "query": result.query,
            "chunks": [chunk.as_citation() for chunk in result.chunks],
            "stats": {
                "dense": result.dense_count,
                "sparse": result.sparse_count,
                "reranked": result.reranked,
                "degraded": result.degraded,
                "elapsed_ms": result.elapsed_ms,
            },
        }
    )


@router.post("/ask")
async def ask(body: AskRequest, db: DB, user: OptionalUser, request: Request):
    await enforce(request, "rag_ask", limit=20)
    answer = await answer_question(
        db,
        body.query,
        book_ids=_book_ids(body),
        history_summary=body.history_summary,
        top_k=body.top_k,
        rerank_top_n=body.rerank_top_n,
        use_rerank=body.use_rerank,
    )
    return ok(answer.as_dict())


@router.post("/ask/stream")
async def ask_stream(body: AskRequest, db: DB, user: OptionalUser, request: Request):
    await enforce(request, "rag_ask", limit=20)
    guard = check_input(body.query)

    async def events():
        if not guard.ok:
            payload = blocked_answer(body.query, guard).as_dict()
            yield _sse({"type": "blocked", "message": payload["answer"]})
            yield _sse({"type": "done", "answer": payload["answer"], "citations": [], "blocked": True})
            return
        async for event in stream_answer(
            db,
            body.query,
            book_ids=_book_ids(body),
            history_summary=body.history_summary,
        ):
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(data: dict) -> str:
    return f"event: {data.get('type', 'message')}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/books/{book_id}/analysis")
async def get_analysis(book_id: int, db: DB, user: OptionalUser):
    book = await db.get(Book, book_id)
    if book is None:
        raise not_found("书籍不存在")
    record = await get_cached(db, book_id)
    if record is None:
        return ok({"book_id": book_id, "status": "absent", "book_title": book.title,
                   "prompt_version": ANALYSIS_PROMPT_VERSION})
    return ok(analysis_out(record, book))


@router.post("/books/{book_id}/analysis", status_code=status.HTTP_201_CREATED)
async def create_analysis(book_id: int, body: AnalysisRequest, db: DB, user: OptionalUser, request: Request):
    await enforce(request, "rag_analysis", limit=6)
    record = await analyze_book(db, book_id, force=body.force, chapter_limit=body.chapter_limit)
    book = await db.get(Book, book_id)
    return ok(analysis_out(record, book), status_code=status.HTTP_201_CREATED)


@router.post("/books/{book_id}/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_book(book_id: int, admin: AdminUser, db: DB):
    report = await ingest_book_id(db, book_id)
    return ok(report.as_dict(), status_code=status.HTTP_202_ACCEPTED)


@router.get("/ingest/tasks")
async def ingest_tasks(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 30):
    rows = (
        await db.execute(select(IngestTask).order_by(IngestTask.id.desc()).limit(limit))
    ).scalars().all()
    return ok(
        [
            {
                "id": task.id,
                "book_id": task.book_id,
                "source_file": task.source_file,
                "status": task.status,
                "stage": task.stage,
                "total_chunks": task.total_chunks,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                "report": task.report,
            }
            for task in rows
        ]
    )


@router.post("/ingest/scan", status_code=status.HTTP_202_ACCEPTED)
async def ingest_scan(admin: AdminUser, db: DB, body: IngestScanRequest):
    reports = await scan_and_ingest(db, limit=body.limit, trigger="admin")
    return ok([report.as_dict() for report in reports], status_code=status.HTTP_202_ACCEPTED)


@router.post("/ingest/upload", status_code=status.HTTP_201_CREATED)
async def ingest_upload(
    admin: AdminUser,
    db: DB,
    file: Annotated[UploadFile, File(description="书名-作者.txt / .md / .pdf")],
    force: Annotated[bool, Query()] = False,
):
    """上传 TXT / Markdown / 文字版 PDF 到语料目录并立即入库。"""
    settings = get_settings()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".txt", ".md", ".pdf"}:
        raise AppError(
            code="UNSUPPORTED_FILE",
            status_code=400,
            message="仅支持 .txt / .md / .pdf 语料",
        )
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AppError(
            code="FILE_TOO_LARGE",
            status_code=413,
            message=f"文件超过 {settings.max_upload_mb}MB",
        )

    corpus = Path(settings.corpus_dir)
    corpus.mkdir(parents=True, exist_ok=True)
    # 浏览器可能把 Windows 路径带进来；只取文件名，避免目录穿越
    safe_name = Path((file.filename or "").replace("\\", "/")).name
    if not safe_name or safe_name.startswith("."):
        raise AppError(code="INVALID_FILENAME", status_code=400, message="文件名不合法")

    target = corpus / (f"{Path(safe_name).stem}.txt" if suffix == ".pdf" else safe_name)
    if target.exists() and not force:
        raise AppError(
            code="FILE_EXISTS",
            status_code=409,
            message="同名语料已存在；如需覆盖请加 force=true",
        )

    converted_from: str | None = None
    if suffix == ".pdf":
        text = extract_pdf_text(data)
        # 保留原始 PDF 便于追溯；扫描目录只认 txt/md，不会重复入库
        originals = corpus / ".originals"
        originals.mkdir(parents=True, exist_ok=True)
        (originals / safe_name).write_bytes(data)
        target.write_text(text, encoding="utf-8")
        converted_from = file.filename
    else:
        target.write_bytes(data)

    report = await ingest_file(db, target, force=force)
    result = report.as_dict()
    if converted_from:
        result["converted_from"] = converted_from
    return ok(result, status_code=status.HTTP_201_CREATED)


@router.get("/bootstrap/latest")
async def bootstrap_latest(admin: AdminUser, db: DB):
    run = await db.scalar(select(BootstrapRun).order_by(BootstrapRun.id.desc()).limit(1))
    if run is None:
        return ok({"status": "none"})
    return ok(
        {
            "id": run.id,
            "trigger": run.trigger,
            "status": run.status,
            "books_seeded": run.books_seeded,
            "books_ingested": run.books_ingested,
            "chunks_created": run.chunks_created,
            "self_check": run.self_check,
            "stages": run.stages,
            "error": run.error,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    )


@router.post("/bootstrap", status_code=status.HTTP_202_ACCEPTED)
async def bootstrap(admin: AdminUser):
    if admin.role != "admin":
        raise forbidden()
    result = await run_bootstrap("admin")
    return ok(result.as_dict(), status_code=status.HTTP_202_ACCEPTED)
