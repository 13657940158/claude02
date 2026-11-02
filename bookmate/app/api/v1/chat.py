"""智能客服接口：会话、消息（普通/流式）、工具调用记录、记忆管理、四域查询。"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.errors import ok
from app.models.chat import ChatSession, ToolCall
from app.services import chat as chat_service
from app.services import domains as domain_service
from app.services import memory as memory_service
from app.services.tools import TOOL_LABELS
from app.utils.limiter import enforce

router = APIRouter(tags=["智能客服"])


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: int | None = None
    book_id: int | None = None
    extract_memory: bool = True


class SessionIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="service", pattern="^(service|companion)$")


class DomainQuery(BaseModel):
    query: str = Field(default="", max_length=200)
    domains: list[str] = Field(default_factory=lambda: ["audio"])


@router.get("/chat/sessions")
async def list_sessions(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return ok(await chat_service.list_sessions(db, user.id, limit))


@router.post("/chat/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionIn, user: CurrentUser, db: DB):
    session = ChatSession(user_id=user.id, title=(body.title or "新对话")[:120], mode=body.mode)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return ok({"id": session.id, "title": session.title, "mode": session.mode},
              status_code=status.HTTP_201_CREATED)


@router.get("/chat/sessions/{session_id}")
async def session_detail(session_id: int, user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    messages = await chat_service.session_messages(db, user, session_id, limit)
    return ok({"session_id": session_id, "messages": messages})


@router.delete("/chat/sessions/{session_id}")
async def close_session(session_id: int, user: CurrentUser, db: DB):
    session = await chat_service.get_or_create_session(db, user, session_id)
    session.status = "closed"
    await db.commit()
    return ok({"id": session.id, "status": session.status})


@router.post("/chat/messages")
async def send_message(body: MessageIn, user: CurrentUser, db: DB, request: Request):
    await enforce(request, "chat", limit=30)
    return ok(
        await chat_service.handle_message(
            db,
            user,
            body.message,
            session_id=body.session_id,
            book_id=body.book_id,
            extract_memory=body.extract_memory,
        )
    )


@router.post("/chat/messages/stream")
async def stream_message(body: MessageIn, user: CurrentUser, db: DB, request: Request):
    await enforce(request, "chat", limit=30)

    async def events():
        async for event in chat_service.stream_message(
            db, user, body.message, session_id=body.session_id, book_id=body.book_id
        ):
            yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/tools")
async def tool_catalog(admin: CurrentUser):
    return ok([{"name": name, "label": label} for name, label in TOOL_LABELS.items()])


@router.get("/chat/tool-calls")
async def tool_calls(admin: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    rows = (
        await db.execute(select(ToolCall).order_by(ToolCall.id.desc()).limit(limit))
    ).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "session_id": row.session_id,
                "tool": row.tool,
                "status": row.status,
                "latency_ms": row.latency_ms,
                "result_size": row.result_size,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/memory")
async def list_memory(user: CurrentUser, db: DB,
                      kind: Annotated[str | None, Query(pattern="^(preference|goal|constraint)$")] = None,
                      limit: Annotated[int, Query(ge=1, le=100)] = 50):
    rows = await memory_service.list_memories(db, user.id, kind, limit)
    return ok([memory_service.memory_out(row) for row in rows])


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int, user: CurrentUser, db: DB):
    deleted = await memory_service.delete_memory(db, user.id, memory_id)
    return ok({"deleted": deleted, "memory_id": memory_id})


@router.post("/domains/query")
async def domain_query(body: DomainQuery, user: CurrentUser, db: DB, request: Request):
    await enforce(request, "domain", limit=60)
    results = await domain_service.call_many(body.domains, body.query, db=db)
    return ok([item.as_dict() for item in results])
