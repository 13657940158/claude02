"""系统自举：建集合 → 管理员 → 种子书 → 语料入库 → 解析 → 检索自检。

设计要点：
- Redis 分布式锁 + BootstrapRun 记录，多进程/重复启动只跑一份；
- 任一步失败不阻断启动，错误写入 BootstrapRun 供 /readyz 与后台展示；
- 幂等：种子书按 (title, author) 去重，入库按 content_hash + 切片唯一键；
- 自检真实走一次混合检索，证明「自动加入书籍并完成检索」。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.book import Book, Chunk
from app.models.ops import BootstrapRun
from app.services.auth import ensure_admin
from app.services.rag import vector_store
from app.services.rag.ingest import scan_and_ingest
from app.services.rag.retrieval import retrieve
from app.scripts.seed_books import seed_books
from app.utils.redis_client import get_redis
from app.utils.timeutil import now_utc

logger = get_logger(__name__)
LOCK_KEY = "bm:bootstrap:lock"
LOCK_TTL = 900


@dataclass
class BootstrapResult:
    run_id: int | None = None
    status: str = "done"
    stages: list[dict] = field(default_factory=list)
    books_seeded: int = 0
    books_ingested: int = 0
    chunks_created: int = 0
    self_check: dict = field(default_factory=dict)
    skipped: bool = False

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "stages": self.stages,
            "books_seeded": self.books_seeded,
            "books_ingested": self.books_ingested,
            "chunks_created": self.chunks_created,
            "self_check": self.self_check,
            "skipped": self.skipped,
        }


async def _acquire_lock() -> bool:
    try:
        redis = get_redis()
        return bool(await redis.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL))
    except Exception as exc:  # noqa: BLE001 - Redis 不可用时允许执行（单实例场景）
        logger.warning("bootstrap_lock_unavailable", extra={"extra_fields": {"error": type(exc).__name__}})
        return True


async def _release_lock() -> None:
    try:
        await get_redis().delete(LOCK_KEY)
    except Exception:  # noqa: BLE001
        pass


async def retrieval_self_check(db: AsyncSession) -> dict:
    """用一本书的首个标题做一个真实检索请求。"""
    started = time.perf_counter()
    row = (
        await db.execute(
            select(Book.id, Book.title)
            .where(Book.chapter_count > 0)
            .order_by(Book.id)
            .limit(1)
        )
    ).first()
    collection = await vector_store.probe_collection()
    if row is None:
        return {
            "ok": False,
            "reason": "no_ingested_books",
            "collection": collection,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    book_id, title = int(row[0]), row[1]
    result = await retrieve(db, f"《{title}》主要讲了什么", book_ids=[book_id], top_k=8, rerank_top_n=4)
    return {
        "ok": bool(result.chunks),
        "book_id": book_id,
        "book_title": title,
        "dense": result.dense_count,
        "sparse": result.sparse_count,
        "kept": len(result.chunks),
        "reranked": result.reranked,
        "degraded": result.degraded,
        "collection": collection,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


async def run_bootstrap(trigger: str = "startup", *, ingest_limit: int | None = None) -> BootstrapResult:
    settings = get_settings()
    result = BootstrapResult()
    if not await _acquire_lock():
        result.skipped = True
        result.status = "running_elsewhere"
        return result

    run = BootstrapRun(trigger=trigger, status="running", stages=[])
    try:
        async with SessionLocal() as db:
            db.add(run)
            await db.commit()
            await db.refresh(run)
            result.run_id = run.id

            async def stage(name: str, status: str = "ok", **payload) -> None:
                entry = {"stage": name, "status": status, **payload}
                result.stages.append(entry)
                run.stages = list(result.stages)
                await db.commit()
                logger.info("bootstrap_stage", extra={"extra_fields": entry})

            # 1. Qdrant 集合
            try:
                info = await vector_store.ensure_collection()
                await stage("vector_collection", collection=info.get("collection"), points=info.get("points"))
            except Exception as exc:  # noqa: BLE001
                await stage("vector_collection", "failed", error=type(exc).__name__)

            # 2. 管理员
            try:
                if settings.admin_email and settings.admin_password:
                    _admin, created = await ensure_admin(db, settings.admin_email, settings.admin_password)
                    await stage("admin", created=created)
                else:
                    await stage("admin", "skipped", reason="ADMIN_EMAIL/ADMIN_PASSWORD 未配置")
            except Exception as exc:  # noqa: BLE001
                await stage("admin", "failed", error=type(exc).__name__)

            # 3. 种子书目
            try:
                created, total = await seed_books(db, settings.seed_book_count)
                result.books_seeded = total
                await stage("seed_books", created=created, total=total)
            except Exception as exc:  # noqa: BLE001
                await stage("seed_books", "failed", error=type(exc).__name__)

            # 4. 自动入库
            if settings.auto_ingest:
                try:
                    reports = await scan_and_ingest(db, limit=ingest_limit, trigger=trigger)
                    result.books_ingested = sum(1 for r in reports if r.status == "done")
                    result.chunks_created = sum(r.chunks_new for r in reports)
                    await stage(
                        "ingest",
                        files=len(reports),
                        done=result.books_ingested,
                        failed=sum(1 for r in reports if r.status == "failed"),
                        chunks_new=result.chunks_created,
                    )
                except Exception as exc:  # noqa: BLE001
                    await stage("ingest", "failed", error=type(exc).__name__)
            else:
                await stage("ingest", "skipped", reason="AUTO_INGEST=false")

            # 5. 检索自检
            try:
                result.self_check = await retrieval_self_check(db)
                await stage("retrieval_self_check", "ok" if result.self_check.get("ok") else "degraded",
                            **{k: v for k, v in result.self_check.items()
                               if k in {"kept", "dense", "sparse", "reranked", "degraded", "reason"}})
            except Exception as exc:  # noqa: BLE001
                result.self_check = {"ok": False, "reason": type(exc).__name__}
                await stage("retrieval_self_check", "failed", error=type(exc).__name__)

            chunks = int(await db.scalar(select(func.count()).select_from(Chunk)) or 0)
            books = int(await db.scalar(select(func.count()).select_from(Book)) or 0)
            result.status = "done" if all(s["status"] != "failed" for s in result.stages) else "partial"
            run.status = result.status
            run.books_seeded = result.books_seeded
            run.books_ingested = result.books_ingested
            run.chunks_created = result.chunks_created
            run.self_check = {**result.self_check, "books_total": books, "chunks_total": chunks}
            run.stages = list(result.stages)
            run.finished_at = now_utc()
            if result.status == "partial":
                errors = [s for s in result.stages if s["status"] == "failed"]
                run.error = "; ".join(f"{s['stage']}:{s.get('error', '')}" for s in errors)[:1000]
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("bootstrap_failed")
        result.status = "failed"
        try:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            run.finished_at = now_utc()
            async with SessionLocal() as db:
                db.add(run)
                await db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        await _release_lock()
    return result


async def run_bootstrap_guarded(
    trigger: str = "startup",
    *,
    ingest_limit: int | None = None,
    timeout: float = 1200,
) -> BootstrapResult:
    """限时自举；超时返回 partial，避免启动容器长期挂起。"""
    try:
        return await asyncio.wait_for(run_bootstrap(trigger, ingest_limit=ingest_limit), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("bootstrap_timeout", extra={"extra_fields": {"timeout": timeout}})
        return BootstrapResult(status="partial", stages=[{"stage": "timeout", "status": "failed"}])


async def bootstrap_in_background() -> None:
    """启动时后台执行，不阻塞健康检查；失败只记日志。"""
    try:
        result = await run_bootstrap_guarded("startup")
        logger.info("bootstrap_finished", extra={"extra_fields": result.as_dict()})
    except Exception:  # noqa: BLE001
        logger.exception("bootstrap_background_failed")


def schedule_startup_bootstrap() -> asyncio.Task | None:
    settings = get_settings()
    if not settings.auto_bootstrap or settings.app_env == "test":
        return None
    return asyncio.create_task(bootstrap_in_background(), name="bookmate-bootstrap")
