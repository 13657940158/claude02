"""在线模型 API 的轻量探测。

只在 /readyz 调用，且不做真实生成（避免每次探活都烧额度）：
- 有 Key 且 URL 合法 → configured
- mock 模式 → mock
真实连通性由 scripts/check_env.py --api 验证。
"""
from typing import Any

from app.core.config import get_settings


def _configured(url: str, key: str) -> dict[str, Any]:
    if not url.strip() or not key.strip():
        return {"status": "not_configured"}
    return {"status": "configured"}


async def probe_online_apis() -> dict[str, Any]:
    settings = get_settings()
    mode = settings.llm_mode.lower()
    if mode == "mock":
        return {
            "llm": {"status": "mock"},
            "embedding": {"status": "mock"},
            "rerank": {"status": "mock"},
        }
    return {
        "llm": {**_configured(settings.llm_base_url, settings.llm_api_key), "model": settings.llm_model},
        "embedding": {
            **_configured(settings.embedding_base_url, settings.embedding_api_key),
            "model": settings.embedding_model,
            "dim": settings.embedding_dim,
        },
        "rerank": {
            **_configured(settings.rerank_base_url, settings.rerank_api_key),
            "model": settings.rerank_model,
            "enabled": settings.rerank_enabled,
        },
    }
