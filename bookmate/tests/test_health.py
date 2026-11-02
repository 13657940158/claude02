"""健康检查与错误模型测试。"""
from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "BookMate"
    assert "uptime_s" in body
    assert resp.headers["X-Request-ID"].startswith("req_")


def test_request_id_passthrough(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"X-Request-ID": "req_custom_123"})
    assert resp.headers["X-Request-ID"] == "req_custom_123"


def test_readyz_structure(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code in {200, 503}
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    assert set(body["checks"]) >= {"mysql", "redis", "qdrant", "llm", "embedding", "rerank"}
    assert isinstance(body["blocking"], list)


def test_api_404_json(client: TestClient) -> None:
    resp = client.get("/api/v1/does-not-exist", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"].startswith("req_")


def test_page_404_html(client: TestClient) -> None:
    resp = client.get("/definitely-not-a-page", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "这一页像书被借走了" in resp.text


def test_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "bookmate_http_requests_total" in resp.text
