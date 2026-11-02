"""健康检查路由（根路径，不走 /api/v1 前缀）。"""
import time

from fastapi import APIRouter
from fastapi.responses import ORJSONResponse

from app import __version__
from app.core.config import get_settings
from app.core.health import readiness

router = APIRouter(tags=["health"])
_started_at = time.time()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "env": settings.app_env,
        "uptime_s": round(time.time() - _started_at, 1),
    }


@router.get("/readyz", include_in_schema=False)
async def readyz() -> ORJSONResponse:
    result = await readiness()
    status_code = 200 if result["status"] == "ok" else 503
    return ORJSONResponse(result, status_code=status_code)
