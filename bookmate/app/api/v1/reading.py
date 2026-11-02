"""导读、伴读、进度、历史与笔记接口。"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser
from app.core.errors import ok
from app.services import companion as companion_service
from app.services import plans as plan_service
from app.utils.limiter import enforce

router = APIRouter(tags=["导读伴读"])


class PlanIn(BaseModel):
    book_id: int
    days: int = Field(default=7, ge=3, le=60)
    daily_minutes: int | None = Field(default=None, ge=10, le=120)
    start_date: date | None = None
    use_llm: bool = True


class CheckIn(BaseModel):
    minutes_spent: int | None = Field(default=None, ge=0, le=600)


class ProgressIn(BaseModel):
    book_id: int
    chapter_no: int | None = Field(default=None, ge=0, le=10000)
    percent: int | None = Field(default=None, ge=0, le=100)
    minutes: int | None = Field(default=None, ge=0, le=600)


class NoteIn(BaseModel):
    book_id: int
    content: str = Field(min_length=1, max_length=5000)
    chapter_no: int | None = Field(default=None, ge=0, le=10000)
    quote: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    companion_session_id: int | None = None


class CompanionIn(BaseModel):
    message: str | None = Field(default=None, max_length=2000)


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanIn, user: CurrentUser, db: DB, request: Request):
    await enforce(request, "plan", limit=10)
    plan, sessions, degraded = await plan_service.create_plan(
        db,
        user,
        body.book_id,
        days=body.days,
        daily_minutes=body.daily_minutes,
        start_date=body.start_date,
        use_llm=body.use_llm,
    )
    from app.models.book import Book

    book = await db.get(Book, body.book_id)
    return ok({**plan_service.plan_out(plan, book, sessions), "degraded": degraded},
              status_code=status.HTTP_201_CREATED)


@router.get("/plans")
async def list_plans(
    user: CurrentUser,
    db: DB,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(active|completed|paused)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return ok(await plan_service.list_plans(db, user.id, status_filter, limit))


@router.get("/plans/{plan_id}")
async def plan_detail(plan_id: int, user: CurrentUser, db: DB):
    return ok(await plan_service.plan_detail(db, user, plan_id))


@router.post("/plans/{plan_id}/checkin")
async def checkin(plan_id: int, user: CurrentUser, db: DB, body: CheckIn | None = None,
                  day_no: Annotated[int | None, Query(ge=1, le=60)] = None):
    plan, _rows = await plan_service.get_plan(db, user, plan_id)
    target = day_no or plan.current_day
    return ok(await plan_service.complete_session(db, user, plan_id, target,
                                                  minutes_spent=(body.minutes_spent if body else None)))


@router.get("/progress")
async def progress_list(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    from sqlalchemy import select

    from app.models.book import Book
    from app.models.reading import Progress

    rows = (
        await db.execute(
            select(Progress).where(Progress.user_id == user.id).order_by(Progress.id.desc()).limit(limit)
        )
    ).scalars().all()
    books = {
        book.id: book
        for book in (await db.execute(select(Book).where(Book.id.in_([r.book_id for r in rows])))).scalars().all()
    } if rows else {}
    return ok([
        {**plan_service.progress_out(row), "book_title": books[row.book_id].title if row.book_id in books else ""}
        for row in rows
    ])


@router.post("/progress")
async def update_progress(body: ProgressIn, user: CurrentUser, db: DB):
    return ok(await plan_service.update_progress(
        db, user, body.book_id, chapter_no=body.chapter_no, percent=body.percent, minutes=body.minutes
    ))


@router.get("/stats")
async def stats(user: CurrentUser, db: DB):
    return ok(await plan_service.reading_stats(db, user.id))


@router.get("/history")
async def history(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    return ok(await plan_service.reading_history(db, user.id, limit))


@router.get("/notes")
async def list_notes(user: CurrentUser, db: DB,
                     book_id: Annotated[int | None, Query(ge=1)] = None,
                     limit: Annotated[int, Query(ge=1, le=200)] = 50):
    rows = await plan_service.list_notes(db, user.id, book_id, limit)
    return ok([plan_service.note_out(row) for row in rows])


@router.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_note(body: NoteIn, user: CurrentUser, db: DB):
    note = await plan_service.create_note(
        db, user, body.book_id, body.content,
        chapter_no=body.chapter_no, quote=body.quote, tags=body.tags,
        companion_session_id=body.companion_session_id,
    )
    return ok(plan_service.note_out(note), status_code=status.HTTP_201_CREATED)


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, user: CurrentUser, db: DB):
    deleted = await plan_service.delete_note(db, user.id, note_id)
    return ok({"deleted": deleted})


@router.post("/companion/plans/{plan_id}/turn")
async def companion_turn(plan_id: int, user: CurrentUser, db: DB, request: Request, body: CompanionIn | None = None):
    await enforce(request, "companion", limit=30)
    return ok(await companion_service.companion_turn(db, user, plan_id, body.message if body else None))


@router.get("/companion/sessions/{session_id}")
async def companion_history(session_id: int, user: CurrentUser, db: DB,
                            limit: Annotated[int, Query(ge=1, le=100)] = 30):
    return ok(await companion_service.companion_history(db, user, session_id, limit))
