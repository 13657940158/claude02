"""认证路由。"""

from fastapi import APIRouter, Request, status

from app.api.deps import DB, CurrentUser, client_ip
from app.core.errors import ok
from app.schemas.auth import (
    ChangePasswordRequest,
    ConsentRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)
from app.services import auth as auth_service
from app.utils.limiter import enforce

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DB):
    await enforce("auth_register", client_ip(request), limit=10, window_seconds=3600)
    user, tokens = await auth_service.register(db, payload)
    return ok(
        {
            "user_id": user.id,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "token_type": "bearer",
            "expires_in": tokens["expires_in"],
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: DB):
    await enforce("auth_login", client_ip(request), limit=20, window_seconds=300)
    user, tokens = await auth_service.login(db, payload)
    tokens["user"] = auth_service.user_out(user)
    return ok(tokens)


@router.post("/refresh")
async def refresh(payload: RefreshRequest, db: DB):
    user, tokens = await auth_service.refresh(db, payload.refresh_token)
    return ok(tokens)


@router.get("/me")
async def me(current: CurrentUser, db: DB):
    from sqlalchemy import select

    from app.models.user import Profile

    profile = await db.scalar(select(Profile).where(Profile.user_id == current.id))
    return ok(
        {
            "user": auth_service.user_out(current),
            "profile": (
                {
                    "dims": profile.dims or {},
                    "goal": profile.goal,
                    "interests": profile.interests or [],
                    "level": profile.level,
                    "daily_minutes": profile.daily_minutes,
                    "confidence": profile.confidence,
                    "version": profile.version,
                    "completed_at": profile.completed_at,
                }
                if profile
                else None
            ),
        }
    )


@router.post("/consent")
async def consent(payload: ConsentRequest, current: CurrentUser, db: DB):
    user = await auth_service.record_consent(db, current, payload.consent, payload.version)
    return ok({"consent_at": user.consent_at, "consent_version": user.consent_version})


@router.post("/change-password")
async def change_password(payload: ChangePasswordRequest, current: CurrentUser, db: DB):
    await auth_service.change_password(db, current, payload.old_password, payload.new_password)
    return ok({"message": "密码已更新，请使用新密码重新登录"})
