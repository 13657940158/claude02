"""书目路由：公开查询 + 管理员维护。"""
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DB, AdminUser, OptionalUser, Pager
from app.core.errors import ok
from app.schemas.book import BookCreate, BookUpdate
from app.services import books as book_service

router = APIRouter(prefix="/books", tags=["书目"])


@router.get("")
async def list_books(
    db: DB,
    pager: Pager,
    user: OptionalUser,
    category: Annotated[str | None, Query(max_length=64)] = None,
    difficulty: Annotated[int | None, Query(ge=1, le=5)] = None,
    level: Annotated[str | None, Query(pattern="^(beginner|intermediate|advanced)$")] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
    sort: Annotated[str, Query(pattern="^(newest|rating|difficulty|minutes|title)$")] = "newest",
):
    items, total = await book_service.list_books(
        db,
        page=pager.page,
        page_size=pager.page_size,
        category=category,
        difficulty=difficulty,
        level=level,
        keyword=q,
        tag=tag,
        sort=sort,
        include_archived=bool(user and user.role == "admin"),
    )
    return ok(
        {
            "items": [
                {
                    "id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "category": book.category,
                    "tags": book.tags or [],
                    "intro": book.intro[:200],
                    "difficulty": book.difficulty,
                    "total_minutes": book.total_minutes,
                    "chapter_count": book.chapter_count,
                    "cover_url": book.cover_url,
                    "rating": book.rating,
                }
                for book in items
            ],
            "total": total,
            "page": pager.page,
            "page_size": pager.page_size,
            "has_next": pager.page * pager.page_size < total,
        }
    )


@router.get("/categories")
async def categories(db: DB):
    return ok(await book_service.categories(db))


@router.get("/{book_id}")
async def get_book(book_id: int, db: DB, user: OptionalUser):
    book = await book_service.get_book(db, book_id, include_archived=bool(user and user.role == "admin"))
    return ok(await book_service.book_detail(db, book))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_book(payload: BookCreate, admin: AdminUser, db: DB):
    book = await book_service.create_book(db, payload, admin.id)
    return ok({"id": book.id, "title": book.title}, status_code=status.HTTP_201_CREATED)


@router.patch("/{book_id}")
async def update_book(book_id: int, payload: BookUpdate, admin: AdminUser, db: DB):
    book = await book_service.get_book(db, book_id, include_archived=True)
    book = await book_service.update_book(db, book, payload, admin.id)
    return ok({"id": book.id, "title": book.title, "status": book.status})


@router.delete("/{book_id}")
async def archive_book(book_id: int, admin: AdminUser, db: DB):
    book = await book_service.get_book(db, book_id, include_archived=True)
    await book_service.archive_book(db, book, admin.id)
    return ok({"id": book.id, "status": book.status})
