"""Qdrant 客户端、集合管理、向量点读写与记忆向量空间。"""
from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    Range,
    VectorParams,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_client: AsyncQdrantClient | None = None


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=30,
            check_compatibility=False,
        )
    return _client


async def close_qdrant() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def memory_collection() -> str:
    return f"{get_settings().collection_name}_memories"


async def probe_collection(name: str | None = None) -> dict[str, Any]:
    """返回集合存在性与点数；集合不存在不算错误。"""
    settings = get_settings()
    name = name or settings.collection_name
    client = get_qdrant()
    collections = await client.get_collections()
    names = {item.name for item in collections.collections}
    if name not in names:
        return {"collection": name, "exists": False, "points": 0}
    info = await client.get_collection(name)
    expected = settings.embedding_dim
    actual = None
    try:
        actual = info.config.params.vectors.size  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - 兼容不同客户端版本
        actual = None
    return {
        "collection": name,
        "exists": True,
        "points": int(info.points_count or 0),
        "dim": actual,
        "dim_ok": actual is None or actual == expected,
    }


async def ensure_collection(recreate_on_dim_mismatch: bool = False) -> dict[str, Any]:
    """幂等创建 chunks 集合与索引。"""
    settings = get_settings()
    client = get_qdrant()
    name = settings.collection_name
    dim = settings.embedding_dim
    exists = await client.collection_exists(name)
    if exists:
        info = await client.get_collection(name)
        try:
            current_dim = info.config.params.vectors.size  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            current_dim = None
        if current_dim is not None and current_dim != dim:
            if not recreate_on_dim_mismatch:
                raise RuntimeError(
                    f"Qdrant 集合 {name} 维度 {current_dim} 与配置 {dim} 不一致；"
                    "请重建集合或修改 EMBEDDING_DIM"
                )
            await client.delete_collection(name)
            exists = False
    if not exists:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("qdrant_collection_created", extra={"extra_fields": {"collection": name, "dim": dim}})
    for field, schema in (
        ("book_id", PayloadSchemaType.INTEGER),
        ("chapter_no", PayloadSchemaType.INTEGER),
        ("chunk_no", PayloadSchemaType.INTEGER),
        ("chunk_id", PayloadSchemaType.INTEGER),
    ):
        try:
            await client.create_payload_index(name, field_name=field, field_schema=schema)
        except Exception:  # noqa: BLE001 - 已存在时忽略
            pass
    mem_name = memory_collection()
    if not await client.collection_exists(mem_name):
        await client.create_collection(
            collection_name=mem_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        try:
            await client.create_payload_index(mem_name, field_name="user_id", field_schema=PayloadSchemaType.INTEGER)
        except Exception:  # noqa: BLE001
            pass
    return await probe_collection(name)


def new_point_id() -> str:
    return str(uuid.uuid4())


async def upsert_chunks(
    points: list[dict[str, Any]],
    batch_size: int = 64,
) -> int:
    """points: [{id, vector, payload}]，分批 upsert；返回写入点数。"""
    client = get_qdrant()
    name = get_settings().collection_name
    written = 0
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        structs = [
            PointStruct(id=item["id"], vector=item["vector"], payload=item["payload"]) for item in batch
        ]
        await client.upsert(collection_name=name, points=structs, wait=True)
        written += len(structs)
    return written


async def delete_chunks(point_ids: list[str]) -> None:
    if not point_ids:
        return
    client = get_qdrant()
    await client.delete(
        collection_name=get_settings().collection_name,
        points_selector=PointIdsList(points=point_ids),
        wait=True,
    )


async def delete_book_points(book_id: int) -> None:
    client = get_qdrant()
    await client.delete(
        collection_name=get_settings().collection_name,
        points_selector=Filter(must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))]),
        wait=True,
    )


def book_filter(
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
) -> Filter | None:
    must: list[Any] = []
    if book_ids and len(book_ids) == 1:
        must.append(FieldCondition(key="book_id", match=MatchValue(value=book_ids[0])))
    if chapter_max is not None:
        must.append(FieldCondition(key="chapter_no", match=Range(lte=chapter_max)))
    return Filter(must=must) if must else None


async def search_chunks(
    vector: list[float],
    limit: int = 20,
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """稠密召回；返回 [{id, score, payload}]。"""
    client = get_qdrant()
    name = get_settings().collection_name
    if not await client.collection_exists(name):
        return []
    if book_ids and len(book_ids) > 1:
        from qdrant_client.models import MatchAny

        query_filter = Filter(
            must=[FieldCondition(key="book_id", match=MatchAny(any=book_ids))]
            + (
                [FieldCondition(key="chapter_no", match=Range(lte=chapter_max))]
                if chapter_max is not None
                else []
            )
        )
    else:
        query_filter = book_filter(book_ids, chapter_max)
    response = await client.query_points(
        collection_name=name,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        score_threshold=score_threshold,
        with_payload=True,
    )
    return [
        {"id": str(hit.id), "score": float(hit.score), "payload": hit.payload or {}}
        for hit in response.points
    ]


async def upsert_memory_vector(memory_id: int, user_id: int, vector: list[float], kind: str) -> str:
    point_id = new_point_id()
    client = get_qdrant()
    await client.upsert(
        collection_name=memory_collection(),
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={"memory_id": memory_id, "user_id": user_id, "kind": kind},
            )
        ],
        wait=True,
    )
    return point_id


async def delete_memory_points(point_ids: list[str]) -> None:
    if not point_ids:
        return
    client = get_qdrant()
    await client.delete(
        collection_name=memory_collection(),
        points_selector=PointIdsList(points=point_ids),
        wait=True,
    )


async def search_memories(
    vector: list[float], user_id: int, limit: int = 5, score_threshold: float = 0.35
) -> list[dict[str, Any]]:
    client = get_qdrant()
    name = memory_collection()
    if not await client.collection_exists(name):
        return []
    response = await client.query_points(
        collection_name=name,
        query=vector,
        limit=limit,
        query_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
        score_threshold=score_threshold,
        with_payload=True,
    )
    return [{"id": str(hit.id), "score": float(hit.score), "payload": hit.payload or {}} for hit in response.points]
