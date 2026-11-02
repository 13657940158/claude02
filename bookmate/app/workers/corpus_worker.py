"""语料巡检 worker：把投放到 CORPUS_DIR 的新书自动切片、向量化并接入检索。

为什么单独起一个进程而不是塞进 API：
- Embedding 走在线 API，单本大书可能跑几分钟；放 API 里会拖慢请求、并随重启中断。
- worker 与 API 共用同一份 MySQL / Qdrant，扫描逻辑复用 `scan_and_ingest`，
  所以「新增入库、变更重建、未变化跳过」与 init 容器完全一致，天然幂等。

运行：
    python -m app.workers.corpus_worker          # 启动扫描一次，之后每 CORPUS_WATCH_SECONDS 扫一次
    python -m app.workers.corpus_worker --once   # 只扫一次后退出（适合 cron / 部署钩子）

CORPUS_WATCH_SECONDS <= 0 时只扫一次。SIGTERM / SIGINT 会等待当前扫描结束再退出，
避免切到一半的切片留在库里。
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionLocal, engine
from app.services.rag.ingest import scan_and_ingest

logger = get_logger(__name__)


async def scan_once() -> dict[str, int]:
    """执行一轮增量扫描，返回 {total, done, failed}。"""
    settings = get_settings()
    async with SessionLocal() as db:
        reports = await scan_and_ingest(db, corpus_dir=settings.corpus_dir, trigger="worker")
    summary = {
        "total": len(reports),
        "done": sum(1 for r in reports if r.status == "done"),
        "failed": sum(1 for r in reports if r.status == "failed"),
        "chunks_new": sum(r.chunks_new for r in reports),
    }
    logger.info("corpus_scan_done", extra={"extra_fields": summary})
    return summary


async def main(once: bool = False) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    interval = max(0, settings.corpus_watch_seconds)
    if once or interval == 0:
        await scan_once()
        await engine.dispose()
        return

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows 本地调试
            loop.add_signal_handler(sig, stop.set)

    logger.info(
        "corpus_worker_started",
        extra={"extra_fields": {"corpus_dir": settings.corpus_dir, "interval_seconds": interval}},
    )
    while not stop.is_set():
        try:
            await scan_once()
        except Exception as exc:  # noqa: BLE001 - 单轮失败不能让 worker 退出
            logger.error(
                "corpus_scan_failed",
                extra={"extra_fields": {"error": type(exc).__name__, "message": str(exc)}},
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)

    logger.info("corpus_worker_stopped")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(once="--once" in sys.argv[1:]))
