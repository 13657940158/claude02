"""书目 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChapterOut(BaseModel):
    id: int
    chapter_no: int
    title: str
    char_count: int
    summary: str | None = None


class BookOut(BaseModel):
    id: int
    title: str
    author: str
    category: str
    tags: list[str] = Field(default_factory=list)
    intro: str
    difficulty: int
    total_minutes: int
    char_count: int
    chapter_count: int
    cover_url: str | None = None
    rating: int = 80
    status: str = "published"
    publish_year: int | None = None
    ingested_at: datetime | None = None


class BookDetailOut(BookOut):
    chapters: list[ChapterOut] = Field(default_factory=list)
    has_analysis: bool = False
    has_corpus: bool = False


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=20)
    intro: str = Field(min_length=10, max_length=5000)
    difficulty: int = Field(default=3, ge=1, le=5)
    total_minutes: int = Field(default=0, ge=0, le=100000)
    cover_url: str | None = Field(default=None, max_length=512)
    publish_year: int | None = Field(default=None, ge=0, le=2100)
    rating: int = Field(default=80, ge=0, le=100)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for tag in value:
            tag = tag.strip()[:32]
            if tag and tag not in seen:
                seen.append(tag)
        return seen[:20]


class BookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    author: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    tags: list[str] | None = None
    intro: str | None = Field(default=None, min_length=10, max_length=5000)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    total_minutes: int | None = Field(default=None, ge=0, le=100000)
    cover_url: str | None = Field(default=None, max_length=512)
    status: str | None = Field(default=None, pattern="^(published|draft|archived)$")
    rating: int | None = Field(default=None, ge=0, le=100)


class BookBrief(BaseModel):
    id: int
    title: str
    author: str
    category: str
    tags: list[str] = Field(default_factory=list)
    difficulty: int
    total_minutes: int
    intro: str = ""
    reason: str = ""
    score: int = 0
