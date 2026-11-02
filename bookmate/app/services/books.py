"""书目查询与维护。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, not_found
from app.models.book import Analysis, Book, Chapter, Chunk
from app.models.governance import AuditLog
from app.schemas.book import BookCreate, BookUpdate
from app.utils.text import normalize_text, sha256_text

SORT_FIELDS = {
    "newest": Book.id.desc(),
    "rating": Book.rating.desc(),
    "difficulty": Book.difficulty.asc(),
    "minutes": Book.total_minutes.asc(),
    "title": Book.title.asc(),
}


async def list_books(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    category: str | None = None,
    difficulty: int | None = None,
    level: str | None = None,
    keyword: str | None = None,
    tag: str | None = None,
    sort: str = "newest",
    include_archived: bool = False,
) -> tuple[list[Book], int]:
    conditions = []
    if not include_archived:
        conditions.append(Book.status == "published")
    if category:
        conditions.append(Book.category == category)
    if difficulty:
        conditions.append(Book.difficulty == difficulty)
    if level == "beginner":
        conditions.append(Book.difficulty <= 2)
    elif level == "intermediate":
        conditions.append(Book.difficulty.between(2, 4))
    elif level == "advanced":
        conditions.append(Book.difficulty >= 4)
    if keyword:
        pattern = f"%{keyword.strip()[:60]}%"
        conditions.append(
            or_(Book.title.like(pattern), Book.author.like(pattern), Book.intro.like(pattern))
        )
    if tag:
        # JSON 数组包含：MySQL JSON_CONTAINS(tags, JSON_QUOTE(:tag))
        conditions.append(func.json_contains(Book.tags, func.json_quote(tag)) == 1)

    base = select(Book).where(*conditions)
    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    order = SORT_FIELDS.get(sort, SORT_FIELDS["newest"])
    rows = await db.execute(
        base.order_by(order).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars().all()), total


async def get_book(db: AsyncSession, book_id: int, include_archived: bool = False) -> Book:
    book = await db.get(Book, book_id)
    if book is None or (not include_archived and book.status == "archived"):
        raise not_found("书籍不存在")
    return book


async def book_detail(db: AsyncSession, book: Book) -> dict:
    chapters = (
        await db.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.chapter_no))
    ).scalars().all()
    chunk_count = int(await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.book_id == book.id)) or 0)
    has_analysis = bool(
        await db.scalar(select(func.count()).select_from(Analysis).where(Analysis.book_id == book.id))
    )
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "category": book.category,
        "tags": book.tags or [],
        "intro": book.intro,
        "difficulty": book.difficulty,
        "total_minutes": book.total_minutes,
        "char_count": book.char_count,
        "chapter_count": book.chapter_count,
        "cover_url": book.cover_url,
        "rating": book.rating,
        "status": book.status,
        "publish_year": book.publish_year,
        "ingested_at": book.ingested_at,
        "chapters": [
            {
                "id": item.id,
                "chapter_no": item.chapter_no,
                "title": item.title,
                "char_count": item.char_count,
                "summary": item.summary,
            }
            for item in chapters
        ],
        "has_analysis": has_analysis,
        "has_corpus": chunk_count > 0,
    }


async def categories(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(Book.category, func.count())
        .where(Book.status == "published")
        .group_by(Book.category)
        .order_by(func.count().desc())
    )
    return [{"name": name, "count": int(count)} for name, count in rows.all()]


async def _ensure_unique(db: AsyncSession, title: str, author: str, exclude_id: int | None = None) -> None:
    stmt = select(Book.id).where(Book.title == title, Book.author == author)
    if exclude_id:
        stmt = stmt.where(Book.id != exclude_id)
    existing = await db.scalar(stmt.limit(1))
    if existing:
        raise conflict("BOOK_EXISTS", f"《{title}》({author}) 已在书目中")


async def create_book(db: AsyncSession, payload: BookCreate, actor_id: int) -> Book:
    title = payload.title.strip()
    author = payload.author.strip()
    await _ensure_unique(db, title, author)
    book = Book(
        title=title,
        author=author,
        category=payload.category.strip(),
        tags=payload.tags,
        intro=normalize_text(payload.intro),
        difficulty=payload.difficulty,
        total_minutes=payload.total_minutes or max(payload.difficulty * 90, 60),
        cover_url=payload.cover_url,
        publish_year=payload.publish_year,
        rating=payload.rating,
        source="admin",
    )
    db.add(book)
    await db.flush()
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="book.create",
            object_type="book",
            object_id=str(book.id),
            after={"title": title, "author": author},
        )
    )
    await db.commit()
    await db.refresh(book)
    return book


async def update_book(db: AsyncSession, book: Book, payload: BookUpdate, actor_id: int) -> Book:
    before = {"title": book.title, "category": book.category, "status": book.status}
    data = payload.model_dump(exclude_unset=True)
    if "title" in data or "author" in data:
        await _ensure_unique(db, data.get("title", book.title), data.get("author", book.author), book.id)
    for key, value in data.items():
        if key == "intro" and value:
            value = normalize_text(value)
        setattr(book, key, value)
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="book.update",
            object_type="book",
            object_id=str(book.id),
            before=before,
            after={key: data.get(key) for key in before if key in data},
        )
    )
    await db.commit()
    await db.refresh(book)
    return book


async def archive_book(db: AsyncSession, book: Book, actor_id: int) -> None:
    book.status = "archived"
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="book.archive",
            object_type="book",
            object_id=str(book.id),
            before={"status": "published"},
            after={"status": "archived"},
        )
    )
    await db.commit()


async def upsert_seed_book(db: AsyncSession, data: dict) -> tuple[Book, bool]:
    """种子导入使用：按 (title, author) 幂等 upsert，不覆盖管理员手工修改的 status。"""
    result = await db.execute(select(Book).where(Book.title == data["title"], Book.author == data["author"]))
    book = result.scalar_one_or_none()
    created = book is None
    if book is None:
        book = Book(**data)
        db.add(book)
    else:
        for key, value in data.items():
            if key in {"title", "author"}:
                continue
            setattr(book, key, value)
    await db.flush()
    return book, created


async def set_content_hash(db: AsyncSession, book: Book, text: str) -> bool:
    """记录语料 hash；返回值表示是否发生变化。"""
    digest = sha256_text(text)
    changed = book.content_hash != digest
    book.content_hash = digest
    return changed
