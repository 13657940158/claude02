"""伴读：只围绕用户已读章节回顾与提问，严格防剧透。

防剧透是硬约束：
- 检索限定 chapter_max=用户已读章节；
- 提示词再次声明 chapter_limit；
- 输出若出现「第 N 章」且 N > limit，或出现后续章节标题，则替换为安全追问（blocked=True）。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, not_found
from app.core.logging import get_logger
from app.models.book import Book, Chapter
from app.models.chat import ChatMessage, ChatSession, CompanionMessage
from app.models.reading import PlanSession, ReadingPlan
from app.models.user import User
from app.services.llm.client import get_llm_client
from app.services.llm.guardrails import check_input
from app.services.llm.prompts import COMPANION_SYSTEM, companion_user_prompt
from app.services.rag.retrieval import chapter_context
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

CHAPTER_MENTION = re.compile(r"第\s*([0-9一二三四五六七八九十百零两]+)\s*[章回节]")
# 书籍常见通用词组，含后文标题也可能是正文高频词（如「无为而治」「上善若水」），不视为剧透
NEUTRAL_TITLE_CHARS = set("的一是了在有和无为而治道圣人不争天地万物")
MIN_TITLE_LEN = 4


def _cn_to_int(text: str) -> int | None:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text.isdigit():
        return int(text)
    if not text:
        return None
    total = 0
    if "百" in text:
        head, _, tail = text.partition("百")
        total += (digits.get(head, 1) if head else 1) * 100
        text = tail
    if "十" in text:
        head, _, tail = text.partition("十")
        total += (digits.get(head, 1) if head else 1) * 10
        text = tail
    if text:
        total += digits.get(text[0], 0)
    return total or None


def spoiler_leak(answer: str, chapter_limit: int, later_titles: list[str]) -> bool:
    for match in CHAPTER_MENTION.finditer(answer or ""):
        number = _cn_to_int(match.group(1))
        if number is not None and number > chapter_limit:
            return True
    for title in later_titles:
        if not title or len(title) < MIN_TITLE_LEN:
            continue
        # 标题若由通用词构成（如「上善若水」「无为而治」），正文与讨论中自然出现，不判剧透
        if all(char in NEUTRAL_TITLE_CHARS for char in title):
            continue
        if title in answer:
            return True
    return False


def safe_followup(chapter_limit: int) -> str:
    return (
        f"我们先把第 {chapter_limit} 章之前的内容聊透："
        "读到这里，有没有哪个细节让你联想到自己的经历？"
        "你可以说说感受，我不会提前讲后面的情节。"
    )


async def get_plan_for_user(db: AsyncSession, user: User, plan_id: int) -> ReadingPlan:
    plan = await db.get(ReadingPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise not_found("计划不存在")
    return plan


async def current_session(db: AsyncSession, plan: ReadingPlan) -> PlanSession | None:
    return await db.scalar(
        select(PlanSession).where(PlanSession.plan_id == plan.id, PlanSession.day_no == plan.current_day)
    )


async def chapter_limit_for_plan(db: AsyncSession, plan: ReadingPlan) -> int:
    """已读上限 = 已完成的最大章节；未开始则允许读当前第一天范围。"""
    done = (
        await db.execute(
            select(PlanSession.chapter_to)
            .where(PlanSession.plan_id == plan.id, PlanSession.status == "done")
        )
    ).scalars().all()
    if done:
        return max(int(value) for value in done)
    session = await current_session(db, plan)
    return session.chapter_from if session else 1


async def _later_titles(db: AsyncSession, book_id: int, chapter_limit: int) -> list[str]:
    rows = (
        await db.execute(
            select(Chapter.title).where(Chapter.book_id == book_id, Chapter.chapter_no > chapter_limit).limit(20)
        )
    ).scalars().all()
    return [title for title in rows if title and len(title) >= 3]


async def ensure_companion_session(db: AsyncSession, user: User, plan: ReadingPlan) -> ChatSession:
    session = await db.scalar(
        select(ChatSession)
        .where(
            ChatSession.user_id == user.id,
            ChatSession.mode == "companion",
            ChatSession.status == "active",
            ChatSession.title.like(f"%计划{plan.id}%"),
        )
        .order_by(ChatSession.id.desc())
        .limit(1)
    )
    if session:
        return session
    session = ChatSession(user_id=user.id, title=f"伴读·计划{plan.id}", mode="companion")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def _history_text(db: AsyncSession, session_id: int, limit: int = 6) -> str:
    rows = (
        await db.execute(
            select(CompanionMessage)
            .where(CompanionMessage.session_id == session_id)
            .order_by(CompanionMessage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return "\n".join(f"{row.role}: {row.content[:200]}" for row in reversed(rows))


async def companion_turn(
    db: AsyncSession,
    user: User,
    plan_id: int,
    message: str | None = None,
) -> dict:
    plan = await get_plan_for_user(db, user, plan_id)
    book = await db.get(Book, plan.book_id)
    if book is None:
        raise not_found("书籍不存在")
    session = await ensure_companion_session(db, user, plan)
    limit = await chapter_limit_for_plan(db, plan)
    current = await current_session(db, plan)

    message = (message or "").strip()
    if message:
        guard = check_input(message)
        if not guard.ok:
            row = CompanionMessage(
                session_id=session.id, plan_id=plan.id, day_no=plan.current_day,
                role="assistant", content=guard.reason, chapter_limit=limit, blocked=True,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return {"session_id": session.id, "message": companion_out(row), "blocked": True}

    contexts = await chapter_context(
        db, plan.book_id, max(1, limit - 1), max(1, limit), limit=6
    )
    history = await _history_text(db, session.id)
    prompt = companion_user_prompt(book.title, limit, plan.current_day, "\n".join(contexts), history)
    if message:
        prompt += f"\n\n用户刚才说：{message}\n请回应 TA，再提出一个问题。"

    blocked = False
    degraded = False
    try:
        answer = await get_llm_client().chat(
            [{"role": "system", "content": COMPANION_SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=350,
            operation="companion",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("companion_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        degraded = True
        focus = current.focus if current else "读到这里，你最想记住什么？"
        answer = f"先回顾一下：{focus}\n\n你今天读到的内容里，哪一句最触动你？"

    later = await _later_titles(db, plan.book_id, limit)
    if spoiler_leak(answer, limit, later):
        logger.warning("companion_spoiler_blocked", extra={"extra_fields": {"plan_id": plan.id, "limit": limit}})
        answer = safe_followup(limit)
        blocked = True

    user_row = None
    if message:
        user_row = CompanionMessage(
            session_id=session.id, plan_id=plan.id, day_no=plan.current_day,
            role="user", content=message[:2000], chapter_limit=limit, kind="answer",
        )
        db.add(user_row)
    assistant = CompanionMessage(
        session_id=session.id,
        plan_id=plan.id,
        day_no=plan.current_day,
        role="assistant",
        content=answer.strip()[:3000],
        chapter_limit=limit,
        refs=contexts[:3],
        kind="question" if not message else "answer",
        blocked=blocked,
    )
    db.add(assistant)
    db.add(
        ChatMessage(
            session_id=session.id,
            user_id=user.id,
            role="user" if message else "system",
            content=(message or "开始今天的伴读")[:4000],
        )
    )
    db.add(
        ChatMessage(
            session_id=session.id,
            user_id=user.id,
            role="assistant",
            content=assistant.content,
            degraded=degraded,
            blocked=blocked,
        )
    )
    session.message_count = int(session.message_count or 0) + 2
    session.last_message_at = now_utc()
    await db.commit()
    await db.refresh(assistant)
    return {
        "session_id": session.id,
        "plan_id": plan.id,
        "day_no": plan.current_day,
        "chapter_limit": limit,
        "message": companion_out(assistant),
        "blocked": blocked,
        "degraded": degraded,
        "current_task": {
            "day_no": current.day_no if current else plan.current_day,
            "chapter_from": current.chapter_from if current else 1,
            "chapter_to": current.chapter_to if current else limit,
            "focus": current.focus if current else "",
            "questions": current.questions if current else [],
        } if current else None,
    }


def companion_out(row: CompanionMessage) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "day_no": row.day_no,
        "chapter_limit": row.chapter_limit,
        "kind": row.kind,
        "refs": row.refs or [],
        "blocked": row.blocked,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def companion_history(db: AsyncSession, user: User, session_id: int, limit: int = 30) -> list[dict]:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise forbidden("无权访问该会话")
    rows = (
        await db.execute(
            select(CompanionMessage)
            .where(CompanionMessage.session_id == session_id)
            .order_by(CompanionMessage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [companion_out(row) for row in reversed(rows)]
