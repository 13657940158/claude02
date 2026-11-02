"""pytest 全局配置：先设置测试环境变量，再导入应用。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试必须覆盖容器/本地 .env 的值，不能只用 setdefault，否则会连真实库、真实在线模型
os.environ["APP_ENV"] = "test"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789"
os.environ["LLM_MODE"] = "mock"
os.environ["DOMAIN_MODE"] = "mock"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "admin_test_2026"
os.environ["CORS_ORIGINS"] = "http://testserver"
os.environ["AUTO_BOOTSTRAP"] = "false"
os.environ["AUTO_INGEST"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
