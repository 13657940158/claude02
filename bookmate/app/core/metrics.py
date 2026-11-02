"""Prometheus 指标与 /metrics 端点。

指标命名遵循 bookmate_<domain>_<name>。业务代码通过本模块的辅助函数打点，
避免在各处直接 import prometheus_client。
"""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "bookmate_http_requests_total",
    "HTTP 请求总数",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "bookmate_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_CALLS = Counter(
    "bookmate_llm_calls_total",
    "在线 LLM 调用次数",
    ["operation", "status"],
)
LLM_LATENCY = Histogram(
    "bookmate_llm_call_duration_seconds",
    "在线 LLM 调用耗时（秒）",
    ["operation"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60),
)
LLM_TOKENS = Counter(
    "bookmate_llm_tokens_total",
    "在线 LLM 消耗 token",
    ["kind"],
)
RAG_RETRIEVALS = Counter(
    "bookmate_rag_retrieval_total",
    "RAG 检索次数",
    ["stage", "status"],
)
INGEST_CHUNKS = Counter(
    "bookmate_ingest_chunks_total",
    "入库 chunk 数",
    ["result"],
)


def record_llm(operation: str, ok: bool, seconds: float, tokens: int = 0) -> None:
    LLM_CALLS.labels(operation=operation, status="ok" if ok else "error").inc()
    LLM_LATENCY.labels(operation=operation).observe(seconds)
    if tokens:
        LLM_TOKENS.labels(kind="total").inc(tokens)


async def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
