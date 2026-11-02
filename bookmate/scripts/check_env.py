#!/usr/bin/env python3
"""环境自检：不需要第三方依赖，可在宿主机或容器内运行。

用法：
  python scripts/check_env.py            # 只检查 .env 变量是否齐全（不联网、不计费）
  python scripts/check_env.py --api      # 额外调用 Embedding / Rerank / LLM 各一次
  python scripts/check_env.py --env-file /path/to/.env

退出码：0 全部通过；1 有缺失或调用失败。
注意：只打印状态与模型名，绝不打印 Key 明文。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUIRED = [
    "APP_SECRET_KEY",
    "ADMIN_PASSWORD",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "RERANK_BASE_URL",
    "RERANK_API_KEY",
    "RERANK_MODEL",
]
PLACEHOLDER_RE = re.compile(r"(your[-_ ]|<.*>|changeme|xxx|placeholder|sk-xxx)", re.IGNORECASE)
DEFAULT_ENV = Path(__file__).resolve().parent.parent / ".env"


def parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def merged_env(path: Path) -> dict[str, str]:
    """进程环境变量优先于 .env 文件（与 docker compose env_file 语义一致）。"""
    env = parse_env(path)
    env.update({k: v for k, v in os.environ.items() if k in REQUIRED})
    return env


def check_required(env: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED:
        value = env.get(key, "").strip()
        if not value:
            problems.append(f"{key} 未填写")
        elif PLACEHOLDER_RE.search(value):
            problems.append(f"{key} 仍是占位符")
    secret = env.get("APP_SECRET_KEY", "")
    if secret and len(secret) < 32:
        problems.append("APP_SECRET_KEY 长度不足 32 字符")
    key = env.get("LLM_API_KEY", "")
    if key and len(key) < 20:
        problems.append("LLM_API_KEY 长度异常（应 ≥ 20）")
    return problems


def _post(url: str, key: str, payload: dict[str, Any], timeout: int = 60) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 用户配置的 API 地址
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        return exc.code, body
    except Exception as exc:  # noqa: BLE001 - 网络错误需要报告而非抛出
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _endpoint(base: str, path: str) -> str:
    return base.rstrip("/") + path


def check_embedding(env: dict[str, str]) -> tuple[bool, str]:
    status, body = _post(
        _endpoint(env["EMBEDDING_BASE_URL"], "/embeddings"),
        env["EMBEDDING_API_KEY"],
        {"model": env["EMBEDDING_MODEL"], "input": ["墨舟自检：读书是为了更好地生活。"]},
    )
    if status != 200:
        return False, f"HTTP {status} {body.get('message') or body.get('error') or ''}".strip()
    vectors = body.get("data") or []
    dim = len(vectors[0].get("embedding", [])) if vectors else 0
    expected = int(env.get("EMBEDDING_DIM") or 0)
    if expected and dim != expected:
        return False, f"维度不符：返回 {dim}，配置 {expected}"
    return True, f"维度 {dim}"


def check_rerank(env: dict[str, str]) -> tuple[bool, str]:
    status, body = _post(
        _endpoint(env["RERANK_BASE_URL"], "/rerank"),
        env["RERANK_API_KEY"],
        {
            "model": env["RERANK_MODEL"],
            "query": "如何养成阅读习惯",
            "documents": ["每天固定时间读十页。", "今天股市大涨。"],
            "top_n": 2,
        },
    )
    if status != 200:
        return False, f"HTTP {status} {body.get('message') or body.get('error') or ''}".strip()
    results = body.get("results") or []
    if not results:
        return False, "返回结果为空"
    top = results[0]
    return True, f"top={top.get('index')} score={round(float(top.get('relevance_score', 0)), 3)}"


def check_llm(env: dict[str, str]) -> tuple[bool, str]:
    status, body = _post(
        _endpoint(env["LLM_BASE_URL"], "/chat/completions"),
        env["LLM_API_KEY"],
        {
            "model": env["LLM_MODEL"],
            "messages": [{"role": "user", "content": "只回复两个字：收到"}],
            "temperature": 0,
            "max_tokens": 16,
        },
    )
    if status != 200:
        return False, f"HTTP {status} {body.get('message') or body.get('error') or ''}".strip()
    choices = body.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    return True, f"回复：{text[:24] or '(空)'}"


def main() -> int:
    parser = argparse.ArgumentParser(description="BookMate 环境自检")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV), help=".env 路径")
    parser.add_argument("--api", action="store_true", help="调用在线模型 API 做连通性验证")
    args = parser.parse_args()

    path = Path(args.env_file)
    env = merged_env(path)
    print(f"[env] {path} {'存在' if path.exists() else '不存在（将使用进程环境变量）'}")

    problems = check_required(env)
    if problems:
        print("[env] 未通过：")
        for item in problems:
            print(f"  - {item}")
    else:
        print("[env] 必填项齐全")

    if not args.api:
        print("[api] 未执行（加 --api 可验证在线模型连通性）")
        return 0 if not problems else 1

    api_ok = True
    if problems:
        print("[api] 跳过：先补齐 .env")
        return 1
    for name, fn in (("embedding", check_embedding), ("rerank", check_rerank), ("llm", check_llm)):
        ok, detail = fn(env)
        api_ok = api_ok and ok
        print(f"[api] {name:<9} {'OK  ' if ok else 'FAIL'} {detail}")

    if problems or not api_ok:
        return 1
    print("[done] 环境自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
