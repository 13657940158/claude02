"""统一响应封装的状态码测试。"""
from app.core.errors import ok


def test_ok_uses_explicit_status_code() -> None:
    # FastAPI 在返回显式 Response 时会忽略路由装饰器的 status_code，
    # 201/202 必须由 ok() 自己带上，否则客户端与 OpenAPI 都看到 200。
    assert ok({"id": 1}).status_code == 200
    assert ok({"id": 1}, status_code=201).status_code == 201
    assert ok({"id": 1}, status_code=202).status_code == 202
