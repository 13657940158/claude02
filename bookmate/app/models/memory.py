"""长期记忆：偏好 / 目标 / 约束。"""
from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (
        Index("idx_mem_user_kind_status", "user_id", "kind", "status"),
        UniqueConstraint("user_id", "content_hash", name="uk_mem_user_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # preference/goal/constraint
    content: Mapped[str] = mapped_column(String(512), nullable=False)
    embedding_id: Mapped[str | None] = mapped_column(String(36))
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    source_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 1-5
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(32))
