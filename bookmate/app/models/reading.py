"""导读计划、课时、进度、阅读历史、笔记。"""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ReadingPlan(Base, TimestampMixin):
    __tablename__ = "reading_plans"
    __table_args__ = (Index("idx_plan_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    daily_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    planned_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    current_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    completed_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="rules")


class PlanSession(Base, TimestampMixin):
    __tablename__ = "plan_sessions"
    __table_args__ = (
        UniqueConstraint("plan_id", "day_no", name="uk_ps_plan_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    day_no: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_from: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_to: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    focus: Mapped[str] = mapped_column(Text, nullable=False, default="")
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    minutes_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Progress(Base, TimestampMixin):
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("user_id", "book_id", name="uk_progress_user_book"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reading")
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_read_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ReadingHistory(Base, TimestampMixin):
    __tablename__ = "reading_history"
    __table_args__ = (Index("idx_rh_user_book", "user_id", "book_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1-5
    review: Mapped[str | None] = mapped_column(Text)
    note_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minutes_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Note(Base, TimestampMixin):
    __tablename__ = "notes"
    __table_args__ = (
        Index("idx_notes_user_book", "user_id", "book_id"),
        Index("idx_notes_session", "companion_session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    companion_session_id: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quote: Mapped[str | None] = mapped_column(Text)
