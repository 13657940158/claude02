"""在线 Embedding（OpenAI 兼容 /embeddings，bge-m3 1024 维）。

- 批量：按 EMBEDDING_BATCH_SIZE 分批，失败重试；
- 缓存：同文本命中 Redis（sha256 键）直接复用，降低费用；
- mock：LLM_MODE=mock 时返回确定性伪向量，便于离线测试。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.services.llm.client import get_http_client
from app.utils.redis_client import get_redis

logger = get_logger(__name__)
_CACHE_TTL = 60 * 60 * 24 * 7
_MAX_CHARS = 4000


def _mock_vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [round(v / norm, 6) for v in raw]


def _cache_key(text: str) -> str:
    model = get_settings().embedding_model
    return "bm:emb:" + hashlib.sha256(f"{model}|{text}".encode("utf-8")).hexdigest()


async def _cache_get_many(texts: list[str]) -> dict[str, list[float]]:
    try:
        redis = get_redis()
        keys = [_cache_key(t) for t in texts]
        values = await redis.mget(keys)
        return {
            text: json.loads(value)
            for text, value in zip(texts, values, strict=True)
            if value
        }
    except Exception as exc:  # noqa: BLE001 - 缓存不可用不影响主流程
        logger.warning("embedding_cache_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        return {}


async def _cache_set_many(pairs: dict[str, list[float]]) -> None:
    if not pairs:
        return
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        for text, vector in pairs.items():
            pipe.set(_cache_key(text), json.dumps(vector, separators=(",", ":")), ex=_CACHE_TTL)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        pass


async def _call_api(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    payload = {"model": settings.embedding_model, "input": texts, "encoding_format": "float"}
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}", "Content-Type": "application/json"}
    url = settings.embedding_base_url.rstrip("/") + "/embeddings"
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = await get_http_client().post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    await asyncio.sleep(0.8 * attempt)
                    continue
                raise AppError(
                    code="EMBEDDING_UPSTREAM_ERROR",
                    status_code=502,
                    message=f"Embedding 调用失败（HTTP {resp.status_code}）",
                )
            data = resp.json().get("data") or []
            if len(data) != len(texts):
                raise AppError(code="EMBEDDING_BAD_RESPONSE", status_code=502, message="Embedding 返回数量不符")
            vectors = [item.get("embedding") or [] for item in data]
            for vector in vectors:
                if len(vector) != settings.embedding_dim:
                    raise AppError(
                        code="EMBEDDING_DIM_MISMATCH",
                        status_code=502,
                        message=f"Embedding 维度 {len(vector)} 与配置 {settings.embedding_dim} 不一致",
                    )
            return [list(map(float, vector)) for vector in vectors]
        except (AppError,):
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                await asyncio.sleep(0.6 * attempt)
                continue
    raise AppError(code="EMBEDDING_UNAVAILABLE", status_code=503, message="Embedding 服务暂时不可用") from last


async def embed_texts(texts: list[str], *, use_cache: bool = True) -> list[list[float]]:
    """返回与输入顺序一致的向量列表。"""
    if not texts:
        return []
    settings = get_settings()
    if settings.llm_mode.lower() == "mock":
        return [_mock_vector(t, settings.embedding_dim) for t in texts]
    cleaned = [(t or "").strip()[:_MAX_CHARS] or "（空）" for t in texts]
    cached = await _cache_get_many(cleaned) if use_cache else {}
    missing = [t for t in dict.fromkeys(cleaned) if t not in cached]
    fresh: dict[str, list[float]] = {}
    batch = max(settings.embedding_batch_size, 1)
    started = time.perf_counter()
    for start in range(0, len(missing), batch):
        part = missing[start : start + batch]
        vectors = await _call_api(part)
        for text, vector in zip(part, vectors, strict=True):
            fresh[text] = vector
    if fresh and use_cache:
        await _cache_set_many(fresh)
    if missing:
        logger.info(
            "embedding_done",
            extra={"extra_fields": {
                "requested": len(texts),
                "api_calls": len(missing),
                "cache_hits": len(texts) - len(missing),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }},
        )
    all_vectors = {**cached, **fresh}
    return [all_vectors[t] for t in cleaned]


async def embed_one(text: str, *, use_cache: bool = True) -> list[float]:
    vectors = await embed_texts([text], use_cache=use_cache)
    return vectors[0] if vectors else []
