"""FastAPI 公共依赖：当前用户、管理员、分页、客户端信息。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import user_id_var
from app.core.errors import forbidden, unauthorized
from app.db.session import get_db
from app.models.user import User
from app.utils.security import decode_token

DB = Annotated[AsyncSession, Depends(get_db)]


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized("认证头格式应为 Bearer <token>")
    return token.strip()


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: DB = None,  # type: ignore[assignment]
) -> User:
    payload = decode_token(_bearer(authorization), "access")
    user_id = int(payload["sub"])
    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized("账号不存在")
    if user.status != "active":
        raise forbidden("账号已被停用")
    user_id_var.set(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
    db: DB = None,  # type: ignore[assignment]
) -> User | None:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization, db)
    except Exception:  # noqa: BLE001 - 可选登录：无效凭证按匿名处理
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_admin(current: CurrentUser) -> User:
    if current.role != "admin":
        raise forbidden("需要管理员权限")
    return current


AdminUser = Annotated[User, Depends(get_admin)]


class Pagination:
    def __init__(self, page: int = 1, page_size: int = 20) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination(
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


Pager = Annotated[Pagination, Depends(pagination)]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:255]


async def user_count(db: AsyncSession) -> int:
    rows = await db.execute(select(User.id).limit(1))
    return len(rows.all())
