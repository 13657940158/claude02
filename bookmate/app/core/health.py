"""健康检查：存活与依赖就绪。

/healthz：进程存活（不查依赖，供容器 HEALTHCHECK 使用）。
/readyz：逐项探测 MySQL / Redis / Qdrant / 在线 API / RAG 状态，返回明细。
"""
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import engine
from app.services.rag.vector_store import probe_collection
from app.services.api_probe import probe_online_apis
from app.utils.redis_client import get_redis

logger = get_logger(__name__)


async def check_mysql() -> dict[str, Any]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - 健康检查必须吞异常并报告
        return {"status": "error", "detail": type(exc).__name__}


async def check_redis() -> dict[str, Any]:
    try:
        client = get_redis()
        await client.ping()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}


async def check_qdrant() -> dict[str, Any]:
    try:
        info = await probe_collection()
        return {"status": "ok", **info}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}


async def readiness() -> dict[str, Any]:
    settings = get_settings()
    checks: dict[str, Any] = {
        "mysql": await check_mysql(),
        "redis": await check_redis(),
        "qdrant": await check_qdrant(),
    }
    checks.update(await probe_online_apis())
    missing = settings.missing_online_keys()
    if missing:
        checks["online_keys"] = {"status": "error", "missing": missing}

    # configured/mock 视为可用；error/not_configured 才阻塞
    blocking = [
        name for name, item in checks.items()
        if item.get("status") not in {"ok", "configured", "mock"}
    ]
    return {
        "status": "ok" if not blocking else "degraded",
        "env": settings.app_env,
        "blocking": blocking,
        "checks": checks,
    }
