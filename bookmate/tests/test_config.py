"""配置解析测试。"""
from app.core.config import Settings


def test_cors_split() -> None:
    settings = Settings(cors_origins="http://a.com, http://b.com")  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_missing_online_keys() -> None:
    settings = Settings(llm_api_key="", embedding_api_key="x", rerank_api_key="")
    assert settings.missing_online_keys() == ["LLM_API_KEY", "RERANK_API_KEY"]


def test_prod_validation_raises() -> None:
    settings = Settings(app_env="prod", app_secret_key="short", llm_api_key="", admin_password="")
    try:
        settings.validate_production()
    except RuntimeError as exc:
        assert "APP_SECRET_KEY" in str(exc)
    else:
        raise AssertionError("生产环境缺少配置时应抛错")
