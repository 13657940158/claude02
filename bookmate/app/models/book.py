"""书目、章节、切片、解析缓存。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Book(Base, TimestampMixin):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("title", "author", name="uk_books_title_author"),
        Index("idx_books_category_status", "category", "status"),
        Index("idx_books_difficulty", "difficulty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    intro: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 1-5
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh")
    publish_year: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=80)  # 0-100 内部质量分
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seed")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_no", name="uk_chapters_book_no"),
        Index("idx_chapters_book", "book_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str | None] = mapped_column(String(64))


class Chunk(Base, TimestampMixin):
    """切片镜像；与 Qdrant 点一一对应，用于引用回查与 BM25 稀疏召回。"""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("book_id", "chunk_no", name="uk_chunks_book_no"),
        Index("idx_chunks_book", "book_id"),
        Index("idx_chunks_chapter", "chapter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qdrant_point_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="done")


class Analysis(Base, TimestampMixin):
    """书籍解析结果缓存（LLM 生成，引用校验后落库）。"""

    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("book_id", name="uk_analyses_book"),
        Index("idx_analyses_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="analysis.v1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
