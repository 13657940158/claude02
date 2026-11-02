"""CLI 公共：asyncio 主循环 + 数据库/HTTP 客户端优雅关闭。"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def _dispose() -> None:
    from app.db.session import engine
    from app.services.llm.client import close_http_client
    from app.services.rag.vector_store import close_qdrant
    from app.utils.redis_client import close_redis

    for closer in (close_redis, close_qdrant, close_http_client, engine.dispose):
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - 退出阶段忽略关闭异常
            pass


def run_main(coro: Callable[[], Awaitable[T]]) -> T:
    async def wrapper() -> T:
        try:
            return await coro()
        finally:
            await _dispose()

    try:
        return asyncio.run(wrapper())
    except KeyboardInterrupt:
        print("[cli] 已中断", file=sys.stderr)
        raise SystemExit(130) from None
