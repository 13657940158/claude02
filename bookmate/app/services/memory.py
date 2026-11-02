"""长期记忆：对话抽取 → 去重落库 → 向量化 → 语义召回。

记忆类型：preference（偏好）/ goal（目标）/ constraint（约束）。
召回 = 向量相似（Qdrant 独立集合）+ 重要性加权；模型不可用时退回最近记忆。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.memory import Memory
from app.services.llm.client import get_llm_client
from app.services.llm.embeddings import embed_texts
from app.services.llm.prompts import MEMORY_SYSTEM, memory_user_prompt
from app.services.rag import vector_store
from app.utils.text import sha256_text
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

VALID_KINDS = {"preference", "goal", "constraint"}


async def _existing_hashes(db: AsyncSession, user_id: int) -> set[str]:
    rows = (await db.execute(select(Memory.content_hash).where(Memory.user_id == user_id))).scalars().all()
    return set(rows)


async def extract_memories(conversation: str) -> list[dict]:
    if len(conversation.strip()) < 8:
        return []
    try:
        data = await get_llm_client().chat_json(
            [
                {"role": "system", "content": MEMORY_SYSTEM},
                {"role": "user", "content": memory_user_prompt(conversation[:4000])},
            ],
            temperature=0.1,
            max_tokens=600,
            operation="memory_extract",
        )
    except Exception as exc:  # noqa: BLE001 - 记忆抽取失败不影响对话
        logger.warning("memory_extract_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        return []
    output = []
    for item in data.get("memories") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        content = str(item.get("content") or "").strip()[:500]
        if kind not in VALID_KINDS or len(content) < 4:
            continue
        try:
            importance = max(1, min(5, int(item.get("importance", 3))))
        except (TypeError, ValueError):
            importance = 3
        output.append({"kind": kind, "content": content, "importance": importance})
    return output[:8]


async def save_memories(
    db: AsyncSession,
    user_id: int,
    items: list[dict],
    *,
    source_type: str = "chat",
    source_id: int | None = None,
) -> list[Memory]:
    if not items:
        return []
    existing = await _existing_hashes(db, user_id)
    fresh: list[Memory] = []
    for item in items:
        content = item["content"].strip()
        digest = sha256_text(content)
        if digest in existing:
            continue
        memory = Memory(
            user_id=user_id,
            kind=item["kind"],
            content=content,
            source_type=source_type,
            source_id=source_id,
            importance=item.get("importance", 3),
            content_hash=digest,
            status="active",
        )
        db.add(memory)
        existing.add(digest)
        fresh.append(memory)
    if not fresh:
        return []
    await db.flush()
    try:
        vectors = await embed_texts([f"{item.kind}: {item.content}" for item in fresh])
        for memory, vector in zip(fresh, vectors, strict=True):
            point_id = await vector_store.upsert_memory_vector(memory.id, user_id, vector, memory.kind)
            memory.embedding_id = point_id
    except Exception as exc:  # noqa: BLE001 - 向量失败仍保留记忆文本
        logger.warning("memory_embed_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    await db.commit()
    return fresh


async def remember_turn(
    db: AsyncSession,
    user_id: int,
    conversation: str,
    *,
    source_id: int | None = None,
) -> list[Memory]:
    items = await extract_memories(conversation)
    return await save_memories(db, user_id, items, source_type="chat", source_id=source_id)


async def recall(
    db: AsyncSession,
    user_id: int,
    query: str,
    *,
    limit: int = 5,
) -> list[Memory]:
    by_id: dict[int, Memory] = {}
    try:
        vectors = await embed_texts([query[:500]])
        hits = await vector_store.search_memories(vectors[0], user_id, limit=limit * 2, score_threshold=0.3)
        for hit in hits:
            payload = hit.get("payload") or {}
            memory_id = int(payload.get("memory_id") or 0)
            if memory_id:
                memory = await db.get(Memory, memory_id)
                if memory and memory.user_id == user_id and memory.status == "active":
                    by_id[memory.id] = memory
    except Exception as exc:  # noqa: BLE001 - 向量召回失败退回最近记忆
        logger.warning("memory_recall_vector_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    if len(by_id) < limit:
        rows = (
            await db.execute(
                select(Memory)
                .where(Memory.user_id == user_id, Memory.status == "active")
                .order_by(Memory.importance.desc(), Memory.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        for memory in rows:
            by_id.setdefault(memory.id, memory)
    memories = sorted(by_id.values(), key=lambda item: (item.importance, item.id), reverse=True)[:limit]
    if memories:
        stamp = now_utc().isoformat()
        for memory in memories:
            memory.last_used_at = stamp
        await db.commit()
    return memories


async def list_memories(db: AsyncSession, user_id: int, kind: str | None = None, limit: int = 50) -> list[Memory]:
    stmt = select(Memory).where(Memory.user_id == user_id, Memory.status == "active")
    if kind:
        stmt = stmt.where(Memory.kind == kind)
    return list(
        (
            await db.execute(stmt.order_by(Memory.importance.desc(), Memory.id.desc()).limit(limit))
        ).scalars().all()
    )


async def delete_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    memory = await db.get(Memory, memory_id)
    if memory is None or memory.user_id != user_id:
        return False
    memory.status = "deleted"
    await db.commit()
    if memory.embedding_id:
        try:
            await vector_store.delete_memory_points([memory.embedding_id])
        except Exception:  # noqa: BLE001
            pass
    return True


def memory_context(memories: list[Memory]) -> str:
    if not memories:
        return ""
    labels = {"preference": "阅读偏好", "goal": "阅读目标", "constraint": "限制条件"}
    return "\n".join(f"- {labels.get(item.kind, item.kind)}：{item.content}" for item in memories)


def memory_out(memory: Memory) -> dict:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "content": memory.content,
        "importance": memory.importance,
        "source_type": memory.source_type,
        "status": memory.status,
        "last_used_at": memory.last_used_at,
        "created_at": memory.created_at,
    }
