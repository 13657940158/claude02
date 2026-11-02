"""公共 schema：分页、任务、提示。"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool = False

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_next=page * page_size < total,
        )


class Message(BaseModel):
    message: str = "ok"


class TaskAccepted(BaseModel):
    task_id: int
    status: str = "pending"
    message: str = "任务已创建"


class TaskStatusOut(BaseModel):
    id: int
    book_id: int
    status: str
    stage: str
    progress: int
    total_chunks: int
    done_chunks: int
    error: str | None = None
    report: dict = Field(default_factory=dict)


class IdOut(BaseModel):
    id: int
