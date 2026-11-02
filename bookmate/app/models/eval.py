"""RAGAS 评测、用例、调优建议、提示词版本。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_runs"
    __table_args__ = (UniqueConstraint("run_name", name="uk_eval_run_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset: Mapped[str] = mapped_column(String(255), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    baseline_run_id: Mapped[int | None] = mapped_column(Integer)
    delta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report_dir: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    detail_csv: Mapped[str | None] = mapped_column(String(512))
    summary_csv: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class EvalCase(Base, TimestampMixin):
    __tablename__ = "eval_cases"
    __table_args__ = (Index("idx_ec_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_key: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ground_truth: Mapped[str] = mapped_column(Text, nullable=False, default="")
    contexts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    faithfulness: Mapped[int | None] = mapped_column(Integer)  # 0-10000 存储，避免浮点误差
    answer_relevancy: Mapped[int | None] = mapped_column(Integer)
    context_precision: Mapped[int | None] = mapped_column(Integer)
    context_recall: Mapped[int | None] = mapped_column(Integer)
    failure_type: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 评测管理页展示用：金标类别、章节命中率、引用条数（对旧数据可为空）
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    chapter_hit_rate: Mapped[int | None] = mapped_column(Integer)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvalAdjustment(Base, TimestampMixin):
    __tablename__ = "eval_adjustments"
    __table_args__ = (Index("idx_ea_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_cases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="P2")
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_note: Mapped[str | None] = mapped_column(Text)


class PromptVersion(Base, TimestampMixin):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uk_prompt_name_version"),
        Index("idx_prompt_active", "name", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer)
    eval_score: Mapped[int | None] = mapped_column(Integer)
