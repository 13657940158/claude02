"""OpenAI 兼容 LLM 客户端：重试、并发限制、指标、mock、流式。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.metrics import record_llm

logger = get_logger(__name__)

_http: httpx.AsyncClient | None = None
_semaphore: asyncio.Semaphore | None = None
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def get_http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            headers={"User-Agent": "BookMate/0.1"},
        )
    return _http


async def close_http_client() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(get_settings().llm_max_concurrency, 1))
    return _semaphore


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = _JSON_FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
        return {"value": value}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise AppError(code="LLM_BAD_JSON", status_code=502, message="模型返回的 JSON 无法解析")


def _mock_answer(messages: list[dict[str, str]], mock_text: str | None) -> str:
    if mock_text is not None:
        return mock_text
    last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return f"（mock 模式）我看到了你的问题：{last[:60]}。当前未调用在线模型。"


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def mock(self) -> bool:
        return self.settings.llm_mode.lower() == "mock"

    async def _post(
        self,
        url: str,
        key: str,
        payload: dict[str, Any],
        *,
        timeout: float,
        operation: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        headers = _auth_headers(key)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = await get_http_client().post(url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    record_llm(
                        operation,
                        True,
                        time.perf_counter() - started,
                        int(usage.get("total_tokens") or 0),
                    )
                    return data
                if resp.status_code in _RETRY_STATUS and attempt < 3:
                    retry_after = resp.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.8 * attempt
                    await asyncio.sleep(min(delay, 8))
                    continue
                message = ""
                try:
                    message = (resp.json().get("message") or resp.json().get("error", {}).get("message") or "")
                except Exception:  # noqa: BLE001
                    message = resp.text[:200]
                record_llm(operation, False, time.perf_counter() - started)
                raise AppError(
                    code="LLM_UPSTREAM_ERROR",
                    status_code=502,
                    message=f"在线模型调用失败（HTTP {resp.status_code}）：{message[:160]}",
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                record_llm(operation, False, time.perf_counter() - started)
                raise AppError(
                    code="LLM_UNAVAILABLE",
                    status_code=503,
                    message="在线模型暂时不可用，请稍后再试",
                ) from exc
        raise AppError(code="LLM_UNAVAILABLE", status_code=503, message="在线模型暂时不可用") from last_error

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 900,
        mock_text: str | None = None,
        operation: str = "chat",
    ) -> str:
        if self.mock:
            record_llm(operation, True, 0.0)
            return _mock_answer(messages, mock_text)
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with _get_semaphore():
            data = await self._post(
                _url(self.settings.llm_base_url, "/chat/completions"),
                self.settings.llm_api_key,
                payload,
                timeout=self.settings.llm_timeout_seconds,
                operation=operation,
            )
        choices = data.get("choices") or []
        if not choices:
            raise AppError(code="LLM_EMPTY", status_code=502, message="模型未返回内容")
        content = choices[0].get("message", {}).get("content") or ""
        if not content.strip():
            raise AppError(code="LLM_EMPTY", status_code=502, message="模型返回空内容")
        return content.strip()

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1600,
        mock_payload: dict[str, Any] | None = None,
        operation: str = "chat_json",
    ) -> dict[str, Any]:
        if self.mock:
            record_llm(operation, True, 0.0)
            return mock_payload or {"mock": True, "message": "mock 模式未调用模型"}
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        async with _get_semaphore():
            data = await self._post(
                _url(self.settings.llm_base_url, "/chat/completions"),
                self.settings.llm_api_key,
                payload,
                timeout=self.settings.llm_timeout_seconds,
                operation=operation,
            )
        choices = data.get("choices") or []
        if not choices:
            raise AppError(code="LLM_EMPTY", status_code=502, message="模型未返回内容")
        return _extract_json(choices[0].get("message", {}).get("content") or "")

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.5,
        max_tokens: int = 900,
        mock_text: str | None = None,
        operation: str = "chat_stream",
    ) -> AsyncIterator[str]:
        if self.mock:
            text = _mock_answer(messages, mock_text)
            for i in range(0, len(text), 12):
                yield text[i : i + 12]
                await asyncio.sleep(0.01)
            return
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        started = time.perf_counter()
        headers = _auth_headers(self.settings.llm_api_key)
        async with _get_semaphore():
            try:
                async with get_http_client().stream(
                    "POST",
                    _url(self.settings.llm_base_url, "/chat/completions"),
                    json=payload,
                    headers=headers,
                    timeout=self.settings.llm_timeout_seconds,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        record_llm(operation, False, time.perf_counter() - started)
                        raise AppError(
                            code="LLM_UPSTREAM_ERROR",
                            status_code=502,
                            message=f"流式模型调用失败（HTTP {resp.status_code}）：{body[:160]!r}",
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {}).get("content") or ""
                        if delta:
                            yield delta
                record_llm(operation, True, time.perf_counter() - started)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                record_llm(operation, False, time.perf_counter() - started)
                raise AppError(code="LLM_UNAVAILABLE", status_code=503, message="在线模型暂时不可用") from exc


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
