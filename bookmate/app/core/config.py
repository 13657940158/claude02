"""应用配置：全部来自环境变量 / .env。

约定：
- 键名与环境变量一一对应（大小写不敏感）。
- 不在导入期因缺少在线 API Key 而崩溃：开发环境允许先跑脚手架，
  生产环境由 validate_production() 在启动时强校验。
"""
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # list 字段默认会先按 JSON 解析，导致 CORS_ORIGINS=a,b 报 SettingsError；
        # 本项目统一支持「逗号分隔」字符串，由下方 before-validator 切分。
        enable_decoding=False,
    )

    # 应用
    app_name: str = "BookMate"
    app_env: str = "dev"
    app_secret_key: str = ""
    app_base_url: str = "http://127.0.0.1:8000"
    app_timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"

    # 数据层
    database_url: str = "mysql+aiomysql://bookmate:bookmate_dev_2026@127.0.0.1:3306/bookmate?charset=utf8mb4"
    database_sync_url: str = "mysql+pymysql://bookmate:bookmate_dev_2026@127.0.0.1:3306/bookmate?charset=utf8mb4"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    collection_name: str = "book_chunks"

    # 在线 LLM / Embedding / Rerank
    llm_mode: str = "real"
    llm_base_url: str = "https://api.siliconflow.cn/v1"
    llm_api_key: str = ""
    llm_model: str = "Qwen/Qwen2.5-72B-Instruct"
    llm_judge_model: str = "Qwen/Qwen2.5-72B-Instruct"
    llm_timeout_seconds: float = 60
    llm_max_concurrency: int = 4

    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    rerank_enabled: bool = True
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_timeout_seconds: float = 15
    rerank_top_n: int = 8

    # 四域业务接口
    domain_mode: str = "mock"
    audio_data_base_url: str = ""
    edu_data_base_url: str = ""
    travel_data_base_url: str = ""
    finance_data_base_url: str = ""
    domain_api_key: str = ""
    domain_timeout_seconds: float = 3

    # 账号与安全
    admin_email: str = "admin@example.com"
    admin_password: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8000"])
    rate_limit_per_minute: int = 60
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 14
    max_upload_mb: int = 20

    # 语料自举与入库
    auto_bootstrap: bool = True
    auto_ingest: bool = True
    corpus_dir: str = "./data/books"
    # worker 增量巡检间隔（秒）；<=0 表示只启动时扫一次
    corpus_watch_seconds: int = 1800
    seed_book_count: int = 12
    task_batch_size: int = 40
    ingest_max_chars: int = 5_000_000

    # 评测
    eval_max_cases: int = 50
    eval_concurrency: int = 3
    eval_dataset: str = "eval/datasets/golden_v1.jsonl"
    eval_report_dir: str = "eval/reports"

    # 部署
    image_tag: str = "dev"
    deploy_host: str = ""
    deploy_user: str = ""
    deploy_dir: str = "/opt/bookmate"
    server_public_url: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors(cls, value: object) -> object:
        # pydantic-settings 会在解析 list 字段前尝试 JSON 解码；
        # 逗号分隔的 URL 不是合法 JSON，这里把它还原为原始字符串再切分。
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("[") and text.endswith("]"):
                return [item.strip().strip("\"'") for item in text[1:-1].split(",") if item.strip()]
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    def missing_online_keys(self) -> list[str]:
        required = {
            "LLM_API_KEY": self.llm_api_key,
            "EMBEDDING_API_KEY": self.embedding_api_key,
            "RERANK_API_KEY": self.rerank_api_key,
        }
        return [name for name, value in required.items() if not value.strip()]

    def validate_production(self) -> None:
        if not self.is_prod:
            return
        problems: list[str] = []
        if len(self.app_secret_key) < 32:
            problems.append("APP_SECRET_KEY 需至少 32 字符")
        problems.extend(f"{name} 未配置" for name in self.missing_online_keys())
        if not self.admin_password:
            problems.append("ADMIN_PASSWORD 未配置")
        if problems:
            raise RuntimeError("生产配置校验失败：" + "；".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
