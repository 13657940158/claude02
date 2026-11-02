"""在线 Rerank（/rerank，bge-reranker-v2-m3）。

失败时返回 None，由调用方按 RRF 顺序降级（不中断检索链路）。
"""
from __future__ import annotations

import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.client import get_http_client

logger = get_logger(__name__)
_MAX_DOC_CHARS = 2000


async def rerank_documents(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[dict[str, Any]] | None:
    """返回按相关度降序的 [{index, relevance_score}]；不可用时返回 None。"""
    settings = get_settings()
    if not documents:
        return []
    if settings.llm_mode.lower() == "mock" or not settings.rerank_enabled:
        # mock：按文档与查询的字面重合度给出确定性分数
        query_chars = set(query)
        scored = [
            {"index": i, "relevance_score": len(query_chars & set(doc)) / max(len(query_chars), 1)}
            for i, doc in enumerate(documents)
        ]
        scored.sort(key=lambda item: item["relevance_score"], reverse=True)
        return scored[: top_n or settings.rerank_top_n]

    payload = {
        "model": settings.rerank_model,
        "query": query[:1024],
        "documents": [doc[:_MAX_DOC_CHARS] for doc in documents],
        "top_n": min(top_n or settings.rerank_top_n, len(documents)),
        "return_documents": False,
    }
    headers = {"Authorization": f"Bearer {settings.rerank_api_key}", "Content-Type": "application/json"}
    url = settings.rerank_base_url.rstrip("/") + "/rerank"
    started = time.perf_counter()
    try:
        resp = await get_http_client().post(url, json=payload, headers=headers,
                                            timeout=settings.rerank_timeout_seconds)
        if resp.status_code != 200:
            logger.warning(
                "rerank_failed",
                extra={"extra_fields": {"status": resp.status_code, "docs": len(documents)}},
            )
            return None
        results = resp.json().get("results") or []
        ranked = [
            {"index": int(item["index"]), "relevance_score": float(item.get("relevance_score") or 0)}
            for item in results
            if 0 <= int(item["index"]) < len(documents)
        ]
        logger.info(
            "rerank_done",
            extra={"extra_fields": {
                "docs": len(documents),
                "returned": len(ranked),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }},
        )
        return ranked
    except Exception as exc:  # noqa: BLE001 - 重排失败必须降级而不是报错
        logger.warning("rerank_error", extra={"extra_fields": {"error": type(exc).__name__}})
        return None
