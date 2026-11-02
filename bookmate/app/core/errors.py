"""统一错误模型与异常处理。

所有业务错误抛 AppError，由全局 handler 转成统一响应体：
{code, message, data, request_id}
"""
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, ORJSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.context import request_id_var
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AppError(Exception):
    code: str
    status_code: int = 400
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message or self.code)


# 常用错误的构造器，避免调用处散落魔法数字
def bad_request(code: str, message: str = "") -> AppError:
    return AppError(code=code, status_code=400, message=message)


def unauthorized(message: str = "请先登录") -> AppError:
    return AppError(code="UNAUTHORIZED", status_code=401, message=message)


def forbidden(message: str = "无权限") -> AppError:
    return AppError(code="FORBIDDEN", status_code=403, message=message)


def not_found(message: str = "资源不存在") -> AppError:
    return AppError(code="NOT_FOUND", status_code=404, message=message)


def conflict(code: str, message: str = "") -> AppError:
    return AppError(code=code, status_code=409, message=message)


def upstream_error(message: str = "上游服务不可用") -> AppError:
    return AppError(code="UPSTREAM_ERROR", status_code=502, message=message)


def degraded(message: str = "依赖不可用") -> AppError:
    return AppError(code="DEGRADED", status_code=503, message=message)


def ok(data: Any = None, message: str = "ok", *, status_code: int = 200) -> ORJSONResponse:
    # 显式 Response 会覆盖路由装饰器的 status_code，201/202 需由调用方传入
    return ORJSONResponse(
        {"code": 0, "message": message, "data": data, "request_id": request_id_var.get()},
        status_code=status_code,
    )


def error_response(err: AppError) -> ORJSONResponse:
    payload: dict[str, Any] = {
        "code": err.code,
        "message": err.message or err.code,
        "data": err.details or None,
        "request_id": request_id_var.get(),
    }
    if err.details.get("fields"):
        payload["fields"] = err.details["fields"]
    return ORJSONResponse(payload, status_code=err.status_code)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> ORJSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error", extra={"extra_fields": {"code": exc.code}})
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> ORJSONResponse:
        fields = [
            {"loc": ".".join(str(p) for p in item["loc"]), "msg": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(
            AppError(code="VALIDATION_ERROR", status_code=422, message="字段校验失败", details={"fields": fields})
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        # 浏览器页面请求（Accept 含 text/html）出错时返回 HTML 错误页，API 请求返回 JSON
        wants_html = "text/html" in (request.headers.get("accept") or "")
        if exc.status_code in {404, 500} and wants_html:
            template = request.app.state.templates.get_template(f"{exc.status_code}.html")
            return HTMLResponse(template.render(request=request, app_env=get_settings().app_env),
                                status_code=exc.status_code)
        mapping = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return error_response(
            AppError(code=mapping.get(exc.status_code, "HTTP_ERROR"), status_code=exc.status_code,
                     message=str(exc.detail))
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("unhandled_exception")
        return error_response(AppError(code="INTERNAL_ERROR", status_code=500, message="服务内部错误"))
