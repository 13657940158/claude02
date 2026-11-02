"""账号与画像模型。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uk_users_email"),
        Index("idx_users_role_status", "role", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(190), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="书友")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user/admin
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    consent_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Profile(Base, TimestampMixin):
    """用户阅读画像：五维性格 + 目标 + 兴趣 + 可投入时间。"""

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 五维人格（0-100）：openness / conscientiousness / extraversion / agreeableness / neuroticism
    dims: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    goal: Mapped[str] = mapped_column(String(32), nullable=False, default="growth")
    interests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="beginner")
    daily_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    preferred_formats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    avoid_topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProfileVersion(Base, TimestampMixin):
    """画像快照，便于回溯推荐依据。"""

    __tablename__ = "profile_versions"
    __table_args__ = (Index("idx_pv_user_version", "user_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dims: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    goal: Mapped[str] = mapped_column(String(32), nullable=False, default="growth")
    interests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="questionnaire")
    reason: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
