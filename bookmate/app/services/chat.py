"""智能客服会话：会话管理 → 记忆召回 → 工具编排 → 生成 → 引用校验 → 记忆回流。"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, forbidden, not_found
from app.core.logging import get_logger
from app.models.chat import ChatMessage, ChatSession
from app.models.user import Profile, User
from app.services import memory as memory_service
from app.services.llm.client import get_llm_client
from app.services.llm.guardrails import check_input, looks_like_refusal, validate_citations
from app.services.llm.prompts import CHAT_SYSTEM, PROMPT_VERSION
from app.services.tools import execute_tool, plan_tools, record_tool_calls
from app.utils.text import estimate_tokens, excerpt
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

MAX_HISTORY_TURNS = 6
TITLE_LIMIT = 30


def _history_messages(rows: list[ChatMessage]) -> list[dict[str, str]]:
    messages = []
    for row in rows[-MAX_HISTORY_TURNS * 2 :]:
        if row.role not in {"user", "assistant"}:
            continue
        messages.append({"role": row.role, "content": row.content[:1500]})
    return messages


def _tool_block(invocations) -> str:
    if not invocations:
        return ""
    lines = []
    for item in invocations:
        lines.append(item.summary_for_prompt())
        payload = item.result or {}
        if payload.get("chunks"):
            for cite in payload["chunks"][:6]:
                lines.append(
                    f"  [{cite.get('index')}] 《{cite.get('book_title')}》"
                    f"第{cite.get('chapter_no')}章 {cite.get('chapter_title') or ''}："
                    f"{str(cite.get('snippet') or '')[:180]}"
                )
        else:
            for ctx in (payload.get("contexts") or [])[:4]:
                lines.append(f"  - {str(ctx)[:600]}")
        for book in payload.get("books") or payload.get("items") or []:
            title = book.get("title") or book.get("name") or ""
            extra = book.get("author") or book.get("reason") or book.get("narrator") or ""
            lines.append(f"  - 《{title}》{('：' + str(extra)[:80]) if extra else ''}")
        for quote in payload.get("quotes") or []:
            lines.append(f"  - 金句：{str(quote.get('text', ''))[:80]}")
    return "\n".join(lines)[:6000]


def _profile_block(profile: Profile | None) -> str:
    if profile is None or not profile.completed_at:
        return ""
    from app.services.questionnaire import profile_text

    return profile_text(profile.dims or {}, profile.goal, profile.interests or [], profile.level, profile.daily_minutes)


async def get_or_create_session(
    db: AsyncSession,
    user: User,
    session_id: int | None = None,
    *,
    mode: str = "service",
    title: str | None = None,
) -> ChatSession:
    if session_id:
        session = await db.get(ChatSession, session_id)
        if session is None:
            raise not_found("会话不存在")
        if session.user_id != user.id:
            raise forbidden("无权访问该会话")
        return session
    active = await db.scalar(
        select(ChatSession)
        .where(ChatSession.user_id == user.id, ChatSession.status == "active", ChatSession.mode == mode)
        .order_by(ChatSession.id.desc())
        .limit(1)
    )
    if active is not None:
        return active
    session = ChatSession(user_id=user.id, title=(title or "新对话")[:120], mode=mode)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions(db: AsyncSession, user_id: int, limit: int = 20) -> list[dict]:
    rows = (
        await db.execute(
            select(ChatSession).where(ChatSession.user_id == user_id).order_by(ChatSession.id.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "mode": row.mode,
            "status": row.status,
            "message_count": row.message_count,
            "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
        }
        for row in rows
    ]


async def session_messages(db: AsyncSession, user: User, session_id: int, limit: int = 50) -> list[dict]:
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise not_found("会话不存在")
    if session.user_id != user.id:
        raise forbidden("无权访问该会话")
    rows = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.desc()).limit(limit)
        )
    ).scalars().all()
    return [message_out(row) for row in reversed(rows)]


def message_out(row: ChatMessage) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "citations": row.citations or [],
        "tool_names": row.tool_names or [],
        "degraded": row.degraded,
        "blocked": row.blocked,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _recent_context(db: AsyncSession, session_id: int) -> str:
    rows = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.desc()).limit(6)
        )
    ).scalars().all()
    return "\n".join(f"{row.role}: {row.content[:300]}" for row in reversed(rows))


async def handle_message(
    db: AsyncSession,
    user: User,
    text: str,
    *,
    session_id: int | None = None,
    book_id: int | None = None,
    extract_memory: bool = True,
) -> dict:
    started = time.perf_counter()
    text = (text or "").strip()
    guard = check_input(text)
    session = await get_or_create_session(db, user, session_id)
    user_msg = ChatMessage(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=text[:4000],
        token_count=estimate_tokens(text),
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    if not guard.ok:
        reply = ChatMessage(
            session_id=session.id,
            user_id=user.id,
            role="assistant",
            content=guard.reason,
            blocked=True,
            degraded=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        db.add(reply)
        await db.commit()
        await db.refresh(reply)
        return {"session_id": session.id, "message": message_out(reply), "tools": []}

    # 会话上下文
    history_rows = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id.desc()).limit(12)
        )
    ).scalars().all()
    history_rows = list(reversed(history_rows))

    profile = await db.get(Profile, user.id)
    memories = await memory_service.recall(db, user.id, text, limit=4)
    tool_names = plan_tools(text, has_book=bool(book_id))
    invocations = []
    if tool_names:
        results = await asyncio.gather(
            *(execute_tool(name, db, query=text, user=user, book_id=book_id) for name in tool_names),
            return_exceptions=True,
        )
        for name, result in zip(tool_names, results, strict=True):
            if hasattr(result, "name"):
                invocations.append(result)
            else:
                logger.warning("tool_exception", extra={"extra_fields": {"tool": name}})

    system_parts = [CHAT_SYSTEM]
    profile_text = _profile_block(profile)
    if profile_text:
        system_parts.append(f"【用户画像】{profile_text}")
    memory_text = memory_service.memory_context(memories)
    if memory_text:
        system_parts.append(f"【长期记忆】\n{memory_text}")
    tool_text = _tool_block(invocations)
    if tool_text:
        system_parts.append(f"【工具结果】（资料编号请在引用时使用结果中的原文编号）\n{tool_text}")

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(_history_messages(history_rows[:-1]))
    messages.append({"role": "user", "content": text})

    degraded = False
    citations: list[dict] = []
    answer_text = ""
    model = "mock" if get_llm_client().mock else get_settings().llm_model
    try:
        answer_text = await get_llm_client().chat(
            messages, temperature=0.5, max_tokens=700, operation="chat_service"
        )
    except Exception as exc:  # noqa: BLE001 - 在线模型失败时给确定性兜底
        logger.warning("chat_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        degraded = True
        answer_text = _fallback_answer(invocations)
        model = "fallback"

    retrieval = next((item for item in invocations if item.name == "retrieve_content"), None)
    if retrieval is not None and retrieval.citations:
        valid, ids = validate_citations(answer_text, len(retrieval.citations))
        if not valid:
            degraded = True
        citations = [item for item in retrieval.citations if item["index"] in ids]
        grounded = bool(citations) and not looks_like_refusal(answer_text)
    else:
        grounded = not degraded

    reply = ChatMessage(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=answer_text.strip(),
        citations=citations,
        tool_names=[item.name for item in invocations],
        token_count=estimate_tokens(answer_text),
        model=model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        degraded=degraded,
    )
    db.add(reply)
    await db.flush()
    await record_tool_calls(
        db, invocations, session_id=session.id, message_id=reply.id, user_id=user.id
    )
    session.message_count = int(
        await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session.id)) or 0
    )
    session.last_message_at = now_utc()
    if session.title in {"新对话", ""}:
        session.title = excerpt(text, limit=TITLE_LIMIT)
    await db.commit()
    await db.refresh(reply)

    if extract_memory:
        conversation = await _recent_context(db, session.id)
        try:
            saved = await memory_service.remember_turn(db, user.id, conversation, source_id=session.id)
            if saved:
                logger.info("memory_saved", extra={"extra_fields": {"user_id": user.id, "count": len(saved)}})
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_save_failed", extra={"extra_fields": {"error": type(exc).__name__}})

    return {
        "session_id": session.id,
        "message": {**message_out(reply), "grounded": grounded},
        "tools": [item.out() for item in invocations],
        "memories_used": [item.content for item in memories],
    }


def _fallback_answer(invocations) -> str:
    if not invocations:
        return "在线客服暂时繁忙，稍后可以再问我。你也可以先去书目页挑一本感兴趣的书。"
    lines = ["在线回答服务暂时不可用，先给你查到的信息："]
    for item in invocations:
        if item.status == "ok":
            lines.append(item.summary_for_prompt())
    lines.append("稍后重试，我会结合你的画像给出更完整的建议。")
    return "\n".join(lines)


async def stream_message(
    db: AsyncSession,
    user: User,
    text: str,
    *,
    session_id: int | None = None,
    book_id: int | None = None,
) -> AsyncIterator[dict]:
    """流式客服：先 meta（会话/工具），再 delta，最后 done。"""
    guard = check_input(text)
    session = await get_or_create_session(db, user, session_id)
    if not guard.ok:
        yield {"type": "blocked", "message": guard.reason, "session_id": session.id}
        return
    user_msg = ChatMessage(session_id=session.id, user_id=user.id, role="user", content=(text or "")[:4000])
    db.add(user_msg)
    await db.commit()

    profile = await db.get(Profile, user.id)
    memories = await memory_service.recall(db, user.id, text, limit=4)
    tool_names = plan_tools(text, has_book=bool(book_id))
    invocations = []
    if tool_names:
        results = await asyncio.gather(
            *(execute_tool(name, db, query=text, user=user, book_id=book_id) for name in tool_names),
            return_exceptions=True,
        )
        invocations = [item for item in results if hasattr(item, "name")]
    yield {
        "type": "meta",
        "session_id": session.id,
        "tools": [item.out() for item in invocations],
        "citations": next((item.citations for item in invocations if item.name == "retrieve_content"), []),
    }

    system_parts = [CHAT_SYSTEM]
    profile_text = _profile_block(profile)
    if profile_text:
        system_parts.append(f"【用户画像】{profile_text}")
    memory_text = memory_service.memory_context(memories)
    if memory_text:
        system_parts.append(f"【长期记忆】\n{memory_text}")
    tool_text = _tool_block(invocations)
    if tool_text:
        system_parts.append(f"【工具结果】\n{tool_text}")
    rows = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id.desc()).limit(12)
        )
    ).scalars().all()
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(_history_messages(list(reversed(rows))[:-1]))
    messages.append({"role": "user", "content": text})

    parts: list[str] = []
    degraded = False
    try:
        async for delta in get_llm_client().stream_chat(messages, temperature=0.5, max_tokens=700,
                                                        operation="chat_stream"):
            parts.append(delta)
            yield {"type": "delta", "text": delta}
    except Exception:  # noqa: BLE001
        degraded = True
        fallback = _fallback_answer(invocations)
        yield {"type": "delta", "text": fallback}
        parts = [fallback]

    answer = "".join(parts).strip()
    reply = ChatMessage(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=answer,
        citations=next((item.citations for item in invocations if item.name == "retrieve_content"), []),
        tool_names=[item.name for item in invocations],
        degraded=degraded,
    )
    db.add(reply)
    await db.flush()
    await record_tool_calls(db, invocations, session_id=session.id, message_id=reply.id, user_id=user.id)
    session.message_count = int(
        await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session.id)) or 0
    )
    session.last_message_at = now_utc()
    if session.title in {"新对话", ""}:
        session.title = excerpt(text, limit=TITLE_LIMIT)
    await db.commit()
    await db.refresh(reply)
    with contextlib.suppress(Exception):
        await memory_service.remember_turn(db, user.id, await _recent_context(db, session.id), source_id=session.id)
    yield {"type": "done", "message": message_out(reply), "degraded": degraded}


def chat_prompt_version() -> str:
    return PROMPT_VERSION


def ensure_text(text: str) -> str:
    if not text.strip():
        raise AppError(code="EMPTY_MESSAGE", status_code=400, message="消息不能为空")
    return text
