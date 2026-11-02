"""管理后台接口：运营概览、用户、审计、业务域调用、配置只读视图。

所有接口要求 admin 角色；配置视图不返回任何密钥，只返回模型名、模式与容量参数。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser
from app.core.config import get_settings
from app.core.errors import ok
from app.models.book import Analysis, Book, Chunk
from app.models.book import Chapter as ChapterModel
from app.models.chat import ChatMessage, ChatSession, ToolCall
from app.models.eval import EvalAdjustment, EvalCase, EvalRun, PromptVersion
from app.models.governance import AuditLog, SecurityEvent
from app.models.memory import Memory
from app.models.ops import BootstrapRun, DomainCall, IngestTask
from app.models.reading import Note, Progress, ReadingHistory, ReadingPlan
from app.models.recommend import Feedback, Recommendation
from app.models.user import Profile, User
from app.utils.timeutil import iso_utc

router = APIRouter(prefix="/admin", tags=["管理后台"])


async def _count(db, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    if conditions:
        stmt = stmt.where(*conditions)
    return int(await db.scalar(stmt) or 0)


@router.get("/overview")
async def overview(admin: AdminUser, db: DB):
    recent_eval = await db.scalar(select(EvalRun).order_by(EvalRun.id.desc()).limit(1))
    recent_bootstrap = await db.scalar(select(BootstrapRun).order_by(BootstrapRun.id.desc()).limit(1))
    return ok({
        "users": {
            "total": await _count(db, User),
            "active": await _count(db, User, User.status == "active"),
            "admins": await _count(db, User, User.role == "admin"),
        },
        "books": {
            "total": await _count(db, Book),
            "published": await _count(db, Book, Book.status == "published"),
            "chapters": await _count(db, ChapterModel),
            "chunks": await _count(db, Chunk),
            "analyses_ready": await _count(db, Analysis, Analysis.status == "ready"),
        },
        "chat": {
            "sessions": await _count(db, ChatSession),
            "messages": await _count(db, ChatMessage),
            "tool_calls": await _count(db, ToolCall),
            "tool_errors": await _count(db, ToolCall, ToolCall.status != "ok"),
        },
        "reading": {
            "plans": await _count(db, ReadingPlan),
            "active_plans": await _count(db, ReadingPlan, ReadingPlan.status == "active"),
            "checkins": await _count(db, ReadingHistory),
            "notes": await _count(db, Note),
            "progress": await _count(db, Progress),
        },
        "personalization": {
            "profiles": await _count(db, Profile),
            "recommendations": await _count(db, Recommendation),
            "feedbacks": await _count(db, Feedback),
            "memories": await _count(db, Memory, Memory.status == "active"),
        },
        "eval": {
            "runs": await _count(db, EvalRun),
            "cases": await _count(db, EvalCase),
            "adjustments_pending": await _count(db, EvalAdjustment, EvalAdjustment.applied.is_(False)),
            "latest": {
                "id": recent_eval.id,
                "run_name": recent_eval.run_name,
                "status": recent_eval.status,
                "metrics": recent_eval.metrics,
                "finished_at": iso_utc(recent_eval.finished_at),
            } if recent_eval else None,
        },
        "bootstrap": {
            "status": recent_bootstrap.status,
            "trigger": recent_bootstrap.trigger,
            "books_ingested": recent_bootstrap.books_ingested,
            "chunks_created": recent_bootstrap.chunks_created,
            "finished_at": iso_utc(recent_bootstrap.finished_at),
        } if recent_bootstrap else None,
        "security_events": await _count(db, SecurityEvent),
    })


@router.get("/users")
async def users(
    admin: AdminUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = (
        await db.execute(select(User).order_by(User.id.desc()).offset(offset).limit(limit))
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "email": row.email,
            "nickname": row.nickname,
            "role": row.role,
            "status": row.status,
            "login_count": row.login_count,
            "last_login_at": iso_utc(row.last_login_at),
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ])


@router.get("/ingest/tasks")
async def ingest_tasks(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 30):
    rows = (await db.execute(select(IngestTask).order_by(IngestTask.id.desc()).limit(limit))).scalars().all()
    return ok([
        {
            "id": row.id,
            "book_id": row.book_id,
            "source_file": row.source_file,
            "status": row.status,
            "stage": row.stage,
            "total_chunks": row.total_chunks,
            "done_chunks": row.done_chunks,
            "error": row.error,
            "started_at": iso_utc(row.started_at),
            "finished_at": iso_utc(row.finished_at),
        }
        for row in rows
    ])


@router.get("/domain-calls")
async def domain_calls(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    rows = (
        await db.execute(select(DomainCall).order_by(DomainCall.id.desc()).limit(limit))
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "domain": row.domain,
            "query": row.query,
            "status": row.status,
            "latency_ms": row.latency_ms,
            "response_summary": row.response_summary,
            "error": row.error,
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ])


@router.get("/security-events")
async def security_events(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    rows = (
        await db.execute(select(SecurityEvent).order_by(SecurityEvent.id.desc()).limit(limit))
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "kind": row.kind,
            "severity": row.severity,
            "user_id": row.user_id,
            "ip": row.ip,
            "detail": row.detail,
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ])


@router.get("/audit-logs")
async def audit_logs(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    rows = (
        await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "user_id": row.user_id,
            "action": row.action,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "note": row.note,
            "ip": row.ip,
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ])


@router.get("/config")
async def config_view(admin: AdminUser):
    settings = get_settings()
    return ok({
        "app_env": settings.app_env,
        "llm_mode": settings.llm_mode,
        "llm_model": settings.llm_model,
        "llm_judge_model": settings.llm_judge_model,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_model": settings.rerank_model,
        "rerank_top_n": settings.rerank_top_n,
        "collection_name": settings.collection_name,
        "domain_mode": settings.domain_mode,
        "auto_bootstrap": settings.auto_bootstrap,
        "eval_dataset": settings.eval_dataset,
        "eval_max_cases": settings.eval_max_cases,
    })


@router.get("/prompts")
async def prompts(admin: AdminUser, db: DB):
    rows = (
        await db.execute(select(PromptVersion).order_by(PromptVersion.id.desc()).limit(100))
    ).scalars().all()
    return ok([
        {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "active": row.active,
            "eval_score": row.eval_score / 10000 if row.eval_score is not None else None,
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ])
