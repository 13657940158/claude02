"""页面与静态资源测试。"""
from fastapi.testclient import TestClient


def test_index_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "墨舟" in resp.text
    assert "做性格与目标问卷" in resp.text


def test_css_served(client: TestClient) -> None:
    resp = client.get("/static/css/main.css")
    assert resp.status_code == 200
    assert "--brand" in resp.text


def test_js_served(client: TestClient) -> None:
    resp = client.get("/static/js/api.js")
    assert resp.status_code == 200
    assert "loadStatus" in resp.text
