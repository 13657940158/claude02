"""基于 Redis 的固定窗口限流。

- 依赖不可用时放行（fail-open），并记录一次 degrade 日志，避免 Redis 故障拖垮全站。
- 键：bm:rl:<scope>:<identity>:<window>；窗口秒数默认 60。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.utils.redis_client import get_redis

logger = get_logger(__name__)


@dataclass
class LimitResult:
    allowed: bool
    remaining: int
    reset_in: int


async def hit(scope: str, identity: str, limit: int, window_seconds: int = 60) -> LimitResult:
    window = int(time.time()) // window_seconds
    key = f"bm:rl:{scope}:{identity}:{window}"
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds + 1)
        remaining = max(limit - int(count), 0)
        return LimitResult(allowed=count <= limit, remaining=remaining, reset_in=window_seconds)
    except Exception as exc:  # noqa: BLE001 - 限流失败不应阻断业务
        logger.warning("rate_limit_degraded", extra={"extra_fields": {"scope": scope, "error": type(exc).__name__}})
        return LimitResult(allowed=True, remaining=limit, reset_in=window_seconds)


async def enforce(scope: str, identity: str, limit: int | None = None, window_seconds: int = 60) -> None:
    limit = limit or get_settings().rate_limit_per_minute
    result = await hit(scope, identity, limit, window_seconds)
    if not result.allowed:
        raise AppError(
            code="RATE_LIMITED",
            status_code=429,
            message="操作太频繁，请稍后再试",
            details={"retry_after": result.reset_in},
        )
