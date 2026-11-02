"""长任务、四域调用、启动自举记录。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class IngestTask(Base, TimestampMixin):
    __tablename__ = "ingest_tasks"
    __table_args__ = (
        Index("idx_task_status_created", "status", "created_at"),
        Index("idx_task_book", "book_id"),
        UniqueConstraint("book_id", "idempotency_key", name="uk_task_book_idem"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="init")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DomainCall(Base, TimestampMixin):
    __tablename__ = "domain_calls"
    __table_args__ = (Index("idx_dc_domain_status", "domain", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class BootstrapRun(Base, TimestampMixin):
    __tablename__ = "bootstrap_runs"
    __table_args__ = (Index("idx_bootstrap_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False, default="startup")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    stages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    books_seeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    books_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    self_check: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
