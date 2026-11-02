"""导入内置书目（幂等）。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.book import Book
from app.scripts.seed_data import SEED_BOOKS

logger = get_logger(__name__)


async def seed_books(db: AsyncSession, limit: int | None = None) -> tuple[int, int]:
    """返回 (新增数, 总书目数)。"""
    created = 0
    items = SEED_BOOKS[: limit or len(SEED_BOOKS)]
    for item in items:
        row = await db.scalar(select(Book).where(Book.title == item["title"], Book.author == item["author"]))
        if row is None:
            db.add(
                Book(
                    title=item["title"],
                    author=item["author"],
                    category=item["category"],
                    tags=item["tags"],
                    intro=item["intro"],
                    difficulty=item["difficulty"],
                    total_minutes=item["total_minutes"],
                    rating=item["rating"],
                    publish_year=item.get("publish_year"),
                    source="seed",
                    status="published",
                )
            )
            created += 1
        else:
            # 已存在：只补齐缺失的推荐语义字段，避免覆盖管理员编辑
            if not row.intro:
                row.intro = item["intro"]
            if not row.tags:
                row.tags = item["tags"]
            if not row.total_minutes:
                row.total_minutes = item["total_minutes"]
    await db.commit()
    total = int(await db.scalar(select(func.count()).select_from(Book)) or 0)
    logger.info("seed_books_done", extra={"extra_fields": {"created": created, "total": total}})
    return created, total
