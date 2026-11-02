"""请求中间件：request_id、耗时日志、安全响应头。"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core import metrics
from app.core.context import request_id_var, user_id_var
from app.core.logging import get_logger

logger = get_logger("app.access")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
        token = request_id_var.set(request_id)
        user_token = user_id_var.set(None)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
            user_id_var.reset(user_token)
        latency_s = time.perf_counter() - started
        latency_ms = round(latency_s * 1000, 1)
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        metrics.HTTP_REQUESTS.labels(
            method=request.method, route=route_label, status=str(response.status_code)
        ).inc()
        metrics.HTTP_LATENCY.labels(method=request.method, route=route_label).observe(latency_s)
        response.headers["X-Request-ID"] = request_id
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if not request.url.path.startswith(("/static/", "/healthz")):
            logger.info(
                "request",
                extra={"extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                }},
            )
        return response
