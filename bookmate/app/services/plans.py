"""导读计划：章节分配 → LLM 生成每日任务 → 规则兜底 → 打卡与进度。

约束：
- 每天章节连续、按顺序覆盖，不跳读；
- 每天 10-60 分钟，计划总时长与书籍预估时长偏差 ≤ 20%；
- 模型输出不合法时完全回退到按字数均分的规则计划（degraded=True）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, conflict, not_found
from app.core.logging import get_logger
from app.models.book import Book, Chapter, Chunk
from app.models.reading import Note, PlanSession, Progress, ReadingHistory, ReadingPlan
from app.models.user import User
from app.services.llm.client import get_llm_client
from app.services.llm.prompts import PLAN_SYSTEM, plan_user_prompt
from app.utils.text import excerpt
from app.utils.timeutil import now_utc, today_local

logger = get_logger(__name__)

MAX_DAYS = 60
MIN_DAYS = 3


class PlanPayloadError(ValueError):
    pass


def _fallback_sessions(chapters: list[Chapter], days: int, daily_minutes: int, total_minutes: int) -> list[dict]:
    """按字数均分 + 连续章节；天多于章节时重复最后一章做复盘。"""
    if not chapters:
        raise PlanPayloadError("没有可分配的章节")
    total_chars = sum(max(c.char_count or 0, 1) for c in chapters)
    target = total_chars / days
    day_chapters: list[list[Chapter]] = []
    if len(chapters) <= days:
        # 章节不够分：每天 1 章，最后的复盘日重复最后一章
        day_chapters = [[chapters[min(day - 1, len(chapters) - 1)]] for day in range(1, days + 1)]
    else:
        index = 0
        for day in range(1, days + 1):
            # 给后面每天至少留 1 章；最后一天的上界就是章节总数
            max_end = len(chapters) - (days - day)
            start_index = index
            chars = 0
            while index < max_end and (index == start_index or chars < target):
                chars += max(chapters[index].char_count or 0, 1)
                index += 1
            day_chapters.append(chapters[start_index:index] or [chapters[-1]])

    sessions: list[dict] = []
    for day, selected in enumerate(day_chapters, start=1):
        minutes = max(10, min(60, round(daily_minutes)))
        sessions.append(
            {
                "day_no": day,
                "chapter_from": selected[0].chapter_no,
                "chapter_to": selected[-1].chapter_no,
                "minutes": minutes,
                "title": f"第 {day} 天：{selected[0].title[:40]}",
                "focus": f"读完第{selected[0].chapter_no}-{selected[-1].chapter_no}章，记录一个打动你的句子。",
                "questions": [
                    f"第{selected[0].chapter_no}章里，哪个观点最挑战你原有的看法？",
                    "如果只能把今天读到的内容讲给朋友听，你会讲哪一点？",
                ],
                "status": "pending",
            }
        )
    return sessions


def _validated_llm_sessions(
    data: dict,
    chapters: list[Chapter],
    days: int,
    daily_minutes: int,
) -> list[dict]:
    raw = data.get("sessions") or []
    if not isinstance(raw, list) or len(raw) != days:
        raise PlanPayloadError("模型返回的天数不匹配")
    numbers = sorted({chapter.chapter_no for chapter in chapters})
    first, last = numbers[0], numbers[-1]
    sessions: list[dict] = []
    expected_start = first
    for item in raw:
        try:
            day_no = int(item["day_no"])
            chapter_from = int(item["chapter_from"])
            chapter_to = int(item["chapter_to"])
            minutes = int(item["minutes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanPayloadError("字段缺失或类型错误") from exc
        if day_no != len(sessions) + 1:
            raise PlanPayloadError("day_no 不连续")
        if not 10 <= minutes <= 60:
            raise PlanPayloadError("每日分钟数越界")
        review_day = expected_start > last
        if review_day:
            # 常见模型行为：最后一天做全书回顾；允许但只作为复盘日
            if not (chapter_from <= first and chapter_to == last):
                raise PlanPayloadError("复盘日范围不合法")
            chapter_from = chapter_to = last
        elif chapter_from != expected_start or chapter_to < chapter_from or chapter_to > last:
            raise PlanPayloadError("章节不连续")
        questions = [str(q).strip()[:120] for q in (item.get("questions") or []) if str(q).strip()][:2]
        sessions.append(
            {
                "day_no": day_no,
                "chapter_from": chapter_from,
                "chapter_to": chapter_to,
                "minutes": minutes,
                "title": str(item.get("title") or f"第 {day_no} 天")[:255],
                "focus": str(item.get("focus") or "")[:500],
                "questions": questions or ["今天读到的内容里，你最想记住的是什么？"],
                "status": "pending",
            }
        )
        expected_start = chapter_to + 1
    if expected_start <= last:
        raise PlanPayloadError("章节没有覆盖完")
    return sessions


def _planned_minutes(sessions: list[dict]) -> int:
    return sum(int(item["minutes"]) for item in sessions)


async def create_plan(
    db: AsyncSession,
    user: User,
    book_id: int,
    *,
    days: int = 7,
    daily_minutes: int | None = None,
    start_date: date | None = None,
    use_llm: bool = True,
) -> tuple[ReadingPlan, list[PlanSession], bool]:
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
    book = await db.get(Book, book_id)
    if book is None:
        raise not_found("书籍不存在")
    chunks = int(await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.book_id == book_id)) or 0)
    if chunks == 0:
        raise AppError(
            code="NO_CORPUS",
            status_code=409,
            message="这本书还没有正文，暂时无法生成导读计划。可先阅读已有解析或换一本。",
        )
    chapters = list(
        (
            await db.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no))
        ).scalars().all()
    )
    if not chapters:
        raise AppError(code="NO_CHAPTERS", status_code=409, message="章节信息缺失，无法生成计划")
    daily = int(daily_minutes or user_profile_minutes(db, user.id) or 30)
    daily = max(10, min(120, daily))
    total_minutes = int(book.total_minutes or sum(c.char_count or 0 for c in chapters) / 400)

    degraded = False
    summary = "按章节顺序与字数均分，保证每天读得完。"
    sessions: list[dict] = []
    if use_llm:
        chapter_payload = [
            {"chapter_no": c.chapter_no, "title": c.title, "char_count": c.char_count or 0} for c in chapters
        ]
        try:
            data = await get_llm_client().chat_json(
                [
                    {"role": "system", "content": PLAN_SYSTEM},
                    {"role": "user", "content": plan_user_prompt(book.title, chapter_payload, days, daily)},
                ],
                temperature=0.3,
                max_tokens=1800,
                operation="reading_plan",
            )
            sessions = _validated_llm_sessions(data, chapters, days, daily)
            summary = str(data.get("summary") or summary)[:500]
        except Exception as exc:  # noqa: BLE001 - 计划必须能生成
            logger.warning(
                "plan_llm_degraded",
                extra={"extra_fields": {"error": type(exc).__name__, "detail": str(exc)[:160]}},
            )
            degraded = True
    if not sessions:
        sessions = _fallback_sessions(chapters, days, daily, total_minutes)
        degraded = True
    planned = _planned_minutes(sessions)

    plan = ReadingPlan(
        user_id=user.id,
        book_id=book_id,
        days=days,
        daily_minutes=daily,
        total_minutes=total_minutes,
        planned_minutes=planned,
        start_date=start_date or today_local(),
        status="active",
        current_day=1,
        summary=summary,
        degraded=degraded,
        source="llm" if not degraded else "rules",
    )
    db.add(plan)
    await db.flush()
    rows = [
        PlanSession(
            plan_id=plan.id,
            day_no=item["day_no"],
            chapter_from=item["chapter_from"],
            chapter_to=item["chapter_to"],
            minutes=item["minutes"],
            title=item["title"],
            focus=item["focus"],
            questions=item["questions"],
            status=item.get("status", "pending"),
        )
        for item in sessions
    ]
    db.add_all(rows)
    await _ensure_progress(db, user.id, book_id, plan.id)
    await _ensure_history(db, user.id, book_id, plan.id)
    await db.commit()
    await db.refresh(plan)
    logger.info(
        "plan_created",
        extra={"extra_fields": {"plan_id": plan.id, "days": days, "degraded": degraded, "minutes": planned}},
    )
    return plan, rows, degraded


async def user_profile_minutes(db: AsyncSession, user_id: int) -> int | None:
    from app.models.user import Profile

    profile = await db.get(Profile, user_id)
    return profile.daily_minutes if profile else None


async def _ensure_progress(db: AsyncSession, user_id: int, book_id: int, plan_id: int) -> Progress:
    progress = await db.scalar(select(Progress).where(Progress.user_id == user_id, Progress.book_id == book_id))
    if progress is None:
        progress = Progress(user_id=user_id, book_id=book_id, plan_id=plan_id, chapter_no=0, percent=0)
        db.add(progress)
    else:
        progress.plan_id = plan_id
    await db.flush()
    return progress


async def _ensure_history(db: AsyncSession, user_id: int, book_id: int, plan_id: int) -> ReadingHistory:
    history = await db.scalar(
        select(ReadingHistory)
        .where(ReadingHistory.user_id == user_id, ReadingHistory.book_id == book_id)
        .order_by(ReadingHistory.id.desc())
        .limit(1)
    )
    if history is None:
        history = ReadingHistory(user_id=user_id, book_id=book_id, plan_id=plan_id, started_at=now_utc())
        db.add(history)
    await db.flush()
    return history


async def list_plans(db: AsyncSession, user_id: int, status: str | None = None, limit: int = 20) -> list[dict]:
    stmt = select(ReadingPlan).where(ReadingPlan.user_id == user_id)
    if status:
        stmt = stmt.where(ReadingPlan.status == status)
    plans = list((await db.execute(stmt.order_by(ReadingPlan.id.desc()).limit(limit))).scalars().all())
    if not plans:
        return []
    book_ids = {plan.book_id for plan in plans}
    books = {book.id: book for book in (await db.execute(select(Book).where(Book.id.in_(book_ids)))).scalars().all()}
    return [plan_out(plan, books.get(plan.book_id)) for plan in plans]


def plan_out(plan: ReadingPlan, book: Book | None = None, sessions: list[PlanSession] | None = None) -> dict:
    data = {
        "id": plan.id,
        "book_id": plan.book_id,
        "book_title": book.title if book else "",
        "author": book.author if book else "",
        "days": plan.days,
        "daily_minutes": plan.daily_minutes,
        "total_minutes": plan.total_minutes,
        "planned_minutes": plan.planned_minutes,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "status": plan.status,
        "current_day": plan.current_day,
        "completed_sessions": plan.completed_sessions,
        "progress_percent": int(round(plan.completed_sessions / max(plan.days, 1) * 100)),
        "summary": plan.summary,
        "degraded": plan.degraded,
        "source": plan.source,
        "created_at": plan.created_at,
    }
    if sessions is not None:
        data["sessions"] = [session_out(row) for row in sessions]
    return data


def session_out(row: PlanSession) -> dict:
    return {
        "id": row.id,
        "day_no": row.day_no,
        "chapter_from": row.chapter_from,
        "chapter_to": row.chapter_to,
        "minutes": row.minutes,
        "title": row.title,
        "focus": row.focus,
        "questions": row.questions or [],
        "status": row.status,
        "completed_at": row.completed_at,
        "minutes_spent": row.minutes_spent,
    }


async def get_plan(db: AsyncSession, user: User, plan_id: int) -> tuple[ReadingPlan, list[PlanSession]]:
    plan = await db.get(ReadingPlan, plan_id)
    if plan is None:
        raise not_found("计划不存在")
    if plan.user_id != user.id:
        raise not_found("计划不存在")
    rows = (
        await db.execute(select(PlanSession).where(PlanSession.plan_id == plan_id).order_by(PlanSession.day_no))
    ).scalars().all()
    return plan, list(rows)


async def plan_detail(db: AsyncSession, user: User, plan_id: int) -> dict:
    plan, rows = await get_plan(db, user, plan_id)
    book = await db.get(Book, plan.book_id)
    progress = await db.scalar(
        select(Progress).where(Progress.user_id == user.id, Progress.book_id == plan.book_id)
    )
    data = plan_out(plan, book, rows)
    data["progress"] = progress_out(progress) if progress else None
    return data


async def complete_session(
    db: AsyncSession,
    user: User,
    plan_id: int,
    day_no: int,
    *,
    minutes_spent: int | None = None,
) -> dict:
    plan, rows = await get_plan(db, user, plan_id)
    session = next((row for row in rows if row.day_no == day_no), None)
    if session is None:
        raise not_found("该天任务不存在")
    if session.status == "done":
        raise conflict("DAY_ALREADY_DONE", f"第 {day_no} 天已经打过卡了")
    spent = max(0, min(600, int(minutes_spent or session.minutes)))
    session.status = "done"
    session.completed_at = now_utc()
    session.minutes_spent = spent
    # 上面已把本天置为 done，查询触发 autoflush 后统计结果已包含本次打卡
    plan.completed_sessions = int(
        await db.scalar(
            select(func.count()).select_from(PlanSession)
            .where(PlanSession.plan_id == plan.id, PlanSession.status == "done")
        )
        or 0
    )
    plan.current_day = min(plan.days, max(plan.current_day, day_no + 1))
    if plan.completed_sessions >= plan.days:
        plan.status = "completed"
    book = await db.get(Book, plan.book_id)
    chapter_no = session.chapter_to
    progress = await _ensure_progress(db, user.id, plan.book_id, plan.id)
    _update_streak(progress, spent)
    progress.chapter_no = max(progress.chapter_no, chapter_no)
    total_chapters = int(book.chapter_count or chapter_no or 1) if book else chapter_no
    progress.percent = max(progress.percent, int(round(chapter_no / max(total_chapters, 1) * 100)))
    if plan.completed_sessions >= plan.days:
        progress.percent = 100
        progress.status = "finished"
    history = await _ensure_history(db, user.id, plan.book_id, plan.id)
    history.minutes_total = int(history.minutes_total or 0) + spent
    if progress.status == "finished":
        history.finished_at = history.finished_at or now_utc()
    await db.commit()
    await db.refresh(plan)
    return {
        "plan": plan_out(plan, book),
        "session": session_out(session),
        "progress": progress_out(progress),
    }


def _update_streak(progress: Progress, minutes: int) -> None:
    today = today_local()
    yesterday = today - timedelta(days=1)
    if progress.last_read_date == today:
        pass
    elif progress.last_read_date == yesterday:
        progress.streak_days = int(progress.streak_days or 0) + 1
    else:
        progress.streak_days = 1
    progress.last_read_date = today
    progress.last_read_at = now_utc()
    progress.total_minutes = int(progress.total_minutes or 0) + max(minutes, 0)


def progress_out(progress: Progress) -> dict:
    return {
        "id": progress.id,
        "book_id": progress.book_id,
        "plan_id": progress.plan_id,
        "chapter_no": progress.chapter_no,
        "percent": progress.percent,
        "status": progress.status,
        "streak_days": progress.streak_days,
        "total_minutes": progress.total_minutes,
        "last_read_date": progress.last_read_date.isoformat() if progress.last_read_date else None,
    }


async def update_progress(
    db: AsyncSession,
    user: User,
    book_id: int,
    *,
    chapter_no: int | None = None,
    percent: int | None = None,
    minutes: int | None = None,
) -> dict:
    if await db.get(Book, book_id) is None:
        raise not_found("书籍不存在")
    progress = await _ensure_progress(db, user.id, book_id, 0)
    if chapter_no is not None:
        progress.chapter_no = max(0, min(10000, int(chapter_no)))
    if percent is not None:
        progress.percent = max(0, min(100, int(percent)))
    if minutes:
        _update_streak(progress, int(minutes))
    else:
        _update_streak(progress, 0)
    if progress.percent >= 100:
        progress.status = "finished"
    await db.commit()
    await db.refresh(progress)
    return progress_out(progress)


async def reading_stats(db: AsyncSession, user_id: int) -> dict:
    plans = int(await db.scalar(select(func.count()).select_from(ReadingPlan).where(ReadingPlan.user_id == user_id)) or 0)
    active = int(
        await db.scalar(
            select(func.count()).select_from(ReadingPlan)
            .where(ReadingPlan.user_id == user_id, ReadingPlan.status == "active")
        )
        or 0
    )
    done_plans = int(
        await db.scalar(
            select(func.count()).select_from(ReadingPlan)
            .where(ReadingPlan.user_id == user_id, ReadingPlan.status == "completed")
        )
        or 0
    )
    minutes = int(await db.scalar(select(func.sum(Progress.total_minutes)).where(Progress.user_id == user_id)) or 0)
    streak = int(await db.scalar(select(func.max(Progress.streak_days)).where(Progress.user_id == user_id)) or 0)
    finished_books = int(
        await db.scalar(
            select(func.count()).select_from(Progress)
            .where(Progress.user_id == user_id, Progress.status == "finished")
        )
        or 0
    )
    notes = int(await db.scalar(select(func.count()).select_from(Note).where(Note.user_id == user_id)) or 0)
    return {
        "plans": plans,
        "active_plans": active,
        "completed_plans": done_plans,
        "finished_books": finished_books,
        "total_minutes": minutes,
        "streak_days": streak,
        "notes": notes,
    }


async def reading_history(db: AsyncSession, user_id: int, limit: int = 50) -> list[dict]:
    rows = (
        await db.execute(
            select(ReadingHistory).where(ReadingHistory.user_id == user_id)
            .order_by(ReadingHistory.id.desc()).limit(limit)
        )
    ).scalars().all()
    if not rows:
        return []
    book_ids = {row.book_id for row in rows}
    books = {b.id: b for b in (await db.execute(select(Book).where(Book.id.in_(book_ids)))).scalars().all()}
    return [
        {
            "id": row.id,
            "book_id": row.book_id,
            "book_title": books[row.book_id].title if row.book_id in books else "",
            "plan_id": row.plan_id,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "abandoned_at": row.abandoned_at,
            "rating": row.rating,
            "review": row.review,
            "minutes_total": row.minutes_total,
            "note_count": row.note_count,
        }
        for row in rows
    ]


async def create_note(
    db: AsyncSession,
    user: User,
    book_id: int,
    content: str,
    *,
    chapter_no: int | None = None,
    quote: str | None = None,
    tags: list[str] | None = None,
    companion_session_id: int | None = None,
) -> Note:
    if await db.get(Book, book_id) is None:
        raise not_found("书籍不存在")
    content = content.strip()
    if not content:
        raise AppError(code="EMPTY_NOTE", status_code=400, message="笔记内容不能为空")
    note = Note(
        user_id=user.id,
        book_id=book_id,
        chapter_no=chapter_no,
        companion_session_id=companion_session_id,
        content=content[:5000],
        quote=(quote or None),
        tags=tags or [],
    )
    db.add(note)
    history = await _ensure_history(db, user.id, book_id, 0)
    history.note_count = int(history.note_count or 0) + 1
    await db.commit()
    await db.refresh(note)
    return note


def note_out(note: Note) -> dict:
    return {
        "id": note.id,
        "book_id": note.book_id,
        "chapter_no": note.chapter_no,
        "content": note.content,
        "quote": note.quote,
        "tags": note.tags or [],
        "companion_session_id": note.companion_session_id,
        "created_at": note.created_at,
    }


async def list_notes(db: AsyncSession, user_id: int, book_id: int | None = None, limit: int = 50) -> list[Note]:
    stmt = select(Note).where(Note.user_id == user_id)
    if book_id:
        stmt = stmt.where(Note.book_id == book_id)
    return list((await db.execute(stmt.order_by(Note.id.desc()).limit(limit))).scalars().all())


async def delete_note(db: AsyncSession, user_id: int, note_id: int) -> bool:
    note = await db.get(Note, note_id)
    if note is None or note.user_id != user_id:
        return False
    await db.delete(note)
    await db.commit()
    return True


async def note_excerpt(text: str) -> str:
    return excerpt(text, limit=80)
