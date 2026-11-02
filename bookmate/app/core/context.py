"""请求级上下文变量。

日志与错误响应都需要 request_id；用 contextvar 避免层层传参。
"""
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)
