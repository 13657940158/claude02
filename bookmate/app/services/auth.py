"""账号服务：注册、登录、令牌刷新、改密、隐私同意。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import conflict, not_found, unauthorized
from app.models.governance import AuditLog
from app.models.user import Profile, User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.utils.timeutil import now_utc

CONSENT_VERSION = "v1"


async def _by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


def token_payload(user: User) -> dict:
    settings = get_settings()
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id, user.role),
        "token_type": "bearer",
        "expires_in": settings.jwt_access_minutes * 60,
    }


async def register(db: AsyncSession, payload: RegisterRequest) -> tuple[User, dict]:
    if not payload.consent:
        raise conflict("CONSENT_REQUIRED", "需同意隐私政策后才能注册")
    if await _by_email(db, payload.email):
        raise conflict("EMAIL_EXISTS", "该邮箱已注册，请直接登录")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        nickname=payload.nickname,
        role="user",
        status="active",
        consent_at=now_utc() if payload.consent else None,
        consent_version=CONSENT_VERSION,
        login_count=1,
        last_login_at=now_utc(),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise conflict("EMAIL_EXISTS", "该邮箱已注册，请直接登录") from exc
    db.add(Profile(user_id=user.id, dims={}, interests=[]))
    await db.commit()
    await db.refresh(user)
    return user, token_payload(user)


async def login(db: AsyncSession, payload: LoginRequest) -> tuple[User, dict]:
    user = await _by_email(db, payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise unauthorized("邮箱或密码不正确")
    if user.status != "active":
        raise unauthorized("账号已被停用，请联系管理员")
    user.last_login_at = now_utc()
    user.login_count = (user.login_count or 0) + 1
    await db.commit()
    await db.refresh(user)
    return user, token_payload(user)


async def refresh(db: AsyncSession, refresh_token: str) -> tuple[User, dict]:
    claims = decode_token(refresh_token, "refresh")
    user = await db.get(User, int(claims["sub"]))
    if user is None or user.status != "active":
        raise unauthorized("账号不可用，请重新登录")
    return user, token_payload(user)


async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str) -> None:
    if not verify_password(old_password, user.password_hash):
        raise unauthorized("原密码不正确")
    user.password_hash = hash_password(new_password)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="user.change_password",
            object_type="user",
            object_id=str(user.id),
        )
    )
    await db.commit()


async def record_consent(db: AsyncSession, user: User, consent: bool, version: str) -> User:
    user.consent_at = now_utc() if consent else None
    user.consent_version = version[:16]
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise not_found("用户不存在")
    return user


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "role": user.role,
        "status": user.status,
        "avatar_url": user.avatar_url,
        "consent_at": user.consent_at,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


def ensure_admin_seed_values() -> tuple[str, str]:
    settings = get_settings()
    return settings.admin_email, settings.admin_password


async def ensure_admin(db: AsyncSession, email: str, password: str, nickname: str = "管理员") -> tuple[User, bool]:
    """幂等创建管理员，返回 (用户, 是否新建)。"""
    existing = await _by_email(db, email.lower())
    if existing:
        if existing.role != "admin":
            existing.role = "admin"
            await db.commit()
        return existing, False
    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        nickname=nickname,
        role="admin",
        status="active",
        consent_at=now_utc(),
        consent_version=CONSENT_VERSION,
    )
    db.add(user)
    await db.flush()
    db.add(Profile(user_id=user.id, dims={}, interests=[]))
    await db.commit()
    await db.refresh(user)
    return user, True
