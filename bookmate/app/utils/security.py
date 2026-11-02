"""密码哈希、JWT、随机令牌。"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import unauthorized

_ALGORITHM = "HS256"
_PASSWORD_MAX_BYTES = 72  # bcrypt 只处理前 72 字节


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:_PASSWORD_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_PASSWORD_MAX_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        return False


def _secret() -> str:
    settings = get_settings()
    secret = settings.app_secret_key or "dev-only-insecure-secret-key-change-me"
    return secret


def create_token(user_id: int, role: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def create_access_token(user_id: int, role: str) -> str:
    minutes = get_settings().jwt_access_minutes
    return create_token(user_id, role, "access", timedelta(minutes=minutes))


def create_refresh_token(user_id: int, role: str) -> str:
    days = get_settings().jwt_refresh_days
    return create_token(user_id, role, "refresh", timedelta(days=days))


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("登录凭证无效") from exc
    if payload.get("typ") != expected_type:
        raise unauthorized("登录凭证类型不正确")
    return payload


def new_id(prefix: str = "") -> str:
    """业务唯一 ID（批次号/幂等键），32 位十六进制可满足 CHAR(36)。"""
    return f"{prefix}{secrets.token_hex(16)}"[:36]
