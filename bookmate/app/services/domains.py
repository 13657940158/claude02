"""四域业务接入：听书 audio / 教育 edu / 文旅 travel / 金融 finance。

统一约定：
- 每个域一个 base_url，GET {base_url}/v1/{path}?q=...，Bearer DOMAIN_API_KEY；
- DOMAIN_MODE=mock 时返回确定性示例数据，便于本地联调与演示；
- 真实调用失败不阻断主流程：返回 degraded=True 与可读提示，并把结果写入 DomainCall 审计表；
- 单次超时 DOMAIN_TIMEOUT_SECONDS，结果缓存 10 分钟（Redis）。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ops import DomainCall
from app.services.llm.client import get_http_client
from app.utils.redis_client import get_redis

logger = get_logger(__name__)

DOMAINS = ("audio", "edu", "travel", "finance")
CACHE_TTL = 600


@dataclass
class DomainResult:
    domain: str
    ok: bool = True
    degraded: bool = False
    source: str = "mock"
    latency_ms: int = 0
    summary: str = ""
    items: list[dict] = field(default_factory=list)
    error: str | None = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "domain": self.domain,
            "ok": self.ok,
            "degraded": self.degraded,
            "source": self.source,
            "latency_ms": self.latency_ms,
            "summary": self.summary,
            "items": self.items,
            "error": self.error,
        }


def _base_url(domain: str) -> str:
    settings = get_settings()
    return {
        "audio": settings.audio_data_base_url,
        "edu": settings.edu_data_base_url,
        "travel": settings.travel_data_base_url,
        "finance": settings.finance_data_base_url,
    }.get(domain, "")


def _cache_key(domain: str, query: str) -> str:
    digest = hashlib.sha256(f"{domain}|{query}".encode()).hexdigest()[:24]
    return f"bm:domain:{domain}:{digest}"


async def _cache_get(domain: str, query: str) -> dict | None:
    try:
        raw = await get_redis().get(_cache_key(domain, query))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


async def _cache_set(domain: str, query: str, data: dict) -> None:
    try:
        await get_redis().set(_cache_key(domain, query), json.dumps(data, ensure_ascii=False), ex=CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


def _mock_payload(domain: str, query: str) -> dict[str, Any]:
    title = query.strip()[:40] or "这本书"
    if domain == "audio":
        return {
            "summary": f"《{title}》有声版可在听书频道收听，适合通勤与睡前。",
            "items": [
                {"type": "audiobook", "title": f"《{title}》有声书", "narrator": "墨舟主播",
                 "duration_minutes": 168, "chapters": 12, "free": True, "link": "mock://audio/demo"},
                {"type": "podcast", "title": "十分钟读懂一本书", "narrator": "编辑部",
                 "duration_minutes": 12, "chapters": 1, "free": True, "link": "mock://audio/podcast"},
            ],
        }
    if domain == "edu":
        return {
            "summary": f"教育频道提供了与《{title}》配套的导读课与练习。",
            "items": [
                {"type": "course", "title": f"《{title}》精读课", "provider": "墨舟学堂",
                 "lessons": 8, "hours": 3.5, "level": "入门", "link": "mock://edu/course"},
                {"type": "worksheet", "title": "阅读反思练习单", "provider": "墨舟学堂",
                 "lessons": 1, "hours": 0.5, "level": "通用", "link": "mock://edu/worksheet"},
            ],
        }
    if domain == "travel":
        return {
            "summary": f"若想边走边读，文旅频道有与《{title}》气质相近的城市阅读路线。",
            "items": [
                {"type": "reading_route", "title": "城市书店漫步半日线", "city": "上海",
                 "stops": 4, "hours": 3, "season": "四季", "link": "mock://travel/route"},
                {"type": "library", "title": "沿江安静阅读空间", "city": "杭州",
                 "stops": 2, "hours": 2, "season": "春秋", "link": "mock://travel/library"},
            ],
        }
    return {
        "summary": f"金融频道提供购书预算与《{title}》相关的理财延伸阅读。",
        "items": [
            {"type": "budget", "title": "年度阅读预算模板", "provider": "墨舟财务",
             "amount": "300-800 元/年", "link": "mock://finance/budget"},
            {"type": "booklist", "title": "财商入门书单", "provider": "墨舟财务",
             "amount": "5 本", "link": "mock://finance/booklist"},
        ],
    }


def _compact_items(domain: str, data: dict) -> list[dict]:
    allowed = {
        "audio": ("title", "narrator", "duration_minutes", "chapters", "free", "link"),
        "edu": ("title", "provider", "lessons", "hours", "level", "link"),
        "travel": ("title", "city", "stops", "hours", "season", "link"),
        "finance": ("title", "provider", "amount", "link"),
    }[domain]
    items = []
    for item in (data.get("items") or [])[:5]:
        if not isinstance(item, dict):
            continue
        cleaned = {key: item.get(key) for key in allowed if key in item}
        cleaned["type"] = item.get("type", "")
        items.append(cleaned)
    return items


async def call_domain(
    domain: str,
    query: str,
    *,
    db: AsyncSession | None = None,
    user_id: int | None = None,
) -> DomainResult:
    settings = get_settings()
    started = time.perf_counter()
    if domain not in DOMAINS:
        return DomainResult(domain=domain, ok=False, error="未知业务域", summary="暂不支持该业务域")

    cached = await _cache_get(domain, query)
    if cached:
        cached["source"] = f"{cached.get('source', 'cache')}:cache"
        cached["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return DomainResult(**{k: cached.get(k) for k in DomainResult.__dataclass_fields__ if k in cached})

    base = _base_url(domain)
    if settings.domain_mode.lower() == "mock" or not base:
        data = _mock_payload(domain, query)
        result = DomainResult(
            domain=domain,
            source="mock",
            summary=data["summary"],
            items=_compact_items(domain, data),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )
    else:
        url = f"{base.rstrip('/')}/v1/{domain}/search"
        headers = {"Authorization": f"Bearer {settings.domain_api_key}"} if settings.domain_api_key else {}
        try:
            resp = await get_http_client().get(
                url, params={"q": query[:120]}, headers=headers, timeout=settings.domain_timeout_seconds
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            result = DomainResult(
                domain=domain,
                source="remote",
                summary=str(data.get("summary") or "")[:300],
                items=_compact_items(domain, data),
                latency_ms=int((time.perf_counter() - started) * 1000),
                raw=data,
            )
        except Exception as exc:  # noqa: BLE001 - 业务域降级
            logger.warning(
                "domain_call_failed",
                extra={"extra_fields": {"domain": domain, "error": type(exc).__name__}},
            )
            result = DomainResult(
                domain=domain,
                ok=False,
                degraded=True,
                source="remote",
                summary=f"{domain} 业务暂不可用，可先用站内书评与解析。",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
            )

    await _cache_set(domain, query, result.as_dict())
    if db is not None:
        db.add(
            DomainCall(
                domain=domain,
                endpoint=_base_url(domain) or f"mock://{domain}",
                query=query[:500],
                status="ok" if result.ok else "error",
                latency_ms=result.latency_ms,
                response_summary=result.summary[:500],
                error=result.error,
            )
        )
        try:
            await db.commit()
        except Exception:  # noqa: BLE001 - 审计失败不影响主流程
            await db.rollback()
    return result


async def call_many(domains: list[str], query: str, db: AsyncSession | None = None) -> list[DomainResult]:
    """顺序调用：共用同一个 AsyncSession，并发 commit 会互相打断。"""
    output: list[DomainResult] = []
    for domain in dict.fromkeys(d for d in domains if d in DOMAINS):
        try:
            output.append(await call_domain(domain, query, db=db))
        except Exception as exc:  # noqa: BLE001
            output.append(
                DomainResult(domain=domain, ok=False, degraded=True, error=type(exc).__name__,
                             summary="业务暂不可用")
            )
    return output


def domain_summary(results: list[DomainResult]) -> str:
    parts = []
    for result in results:
        if result.ok and result.summary:
            parts.append(f"[{result.domain}] {result.summary}")
        elif result.error:
            parts.append(f"[{result.domain}] 暂不可用")
    return "\n".join(parts)
