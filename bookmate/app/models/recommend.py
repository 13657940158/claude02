"""推荐记录与反馈。"""
from sqlalchemy import JSON, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("idx_rec_user_created", "user_id", "created_at"),
        Index("idx_rec_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100
    rule_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    factors: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="rules")


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", "action", name="uk_fb_user_book_action"),
        Index("idx_fb_book", "book_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # like/dislike/save
    comment: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="recommend")
