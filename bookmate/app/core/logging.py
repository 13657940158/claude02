"""结构化 JSON 日志。

字段：ts / level / logger / request_id / user_id / msg / 额外字段。
敏感字段（password、token、key、content）在 _sanitize 中脱敏或截断。
"""
import json
import logging
import sys

from app.core.context import request_id_var, user_id_var

_SENSITIVE = ("password", "token", "api_key", "secret", "authorization")
_TRUNCATE_LIMIT = 200


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        user_id = user_id_var.get()
        if user_id is not None:
            payload["user_id"] = user_id
        extras = getattr(record, "extra_fields", None)
        if isinstance(extras, dict):
            payload.update(_sanitize(extras))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(payload, ensure_ascii=False)


def _sanitize(data: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in _SENSITIVE):
            cleaned[key] = "***"
            continue
        if isinstance(value, str) and len(value) > _TRUNCATE_LIMIT:
            cleaned[key] = value[:_TRUNCATE_LIMIT] + "…"
            continue
        cleaned[key] = value
    return cleaned


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # 降噪：第三方库
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
