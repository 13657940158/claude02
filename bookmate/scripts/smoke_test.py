#!/usr/bin/env python3
"""部署后烟雾测试：健康、页面、书目、管理员接口，可选跑一次真实 RAG 问答。

设计：
- 只读为主，不写入业务数据（管理员登录仅签发 token）；
- `--with-llm` 才会调用在线 Embedding / Rerank / LLM，产生少量费用；
- 任一检查失败返回非 0，供 deploy.sh 判断是否回滚。

用法：
    python scripts/smoke_test.py --base-url http://127.0.0.1:8080
    python scripts/smoke_test.py --base-url https://bookmate.example.com --with-llm
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True
    extra: dict = field(default_factory=dict)


def _get(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    return client.get(path, **kwargs)


def _data(resp: httpx.Response):
    """解包统一响应 {code, message, data}；失败时抛出带上下文的异常。"""
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"{payload.get('code')}: {payload.get('message')}")
    return payload.get("data")


def run(base_url: str, timeout: float, with_llm: bool) -> list[Check]:
    checks: list[Check] = []
    settings = get_settings()
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, follow_redirects=True) as client:
        # 1. 存活
        try:
            resp = _get(client, "/healthz")
            body = resp.json()
            checks.append(Check("healthz", resp.status_code == 200 and body.get("status") == "ok",
                                f"{resp.status_code} {body.get('version', '')}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("healthz", False, type(exc).__name__))
            return checks  # 进程都没起来，后续没有意义

        # 2. 依赖就绪
        try:
            resp = _get(client, "/readyz")
            body = resp.json()
            blocking = body.get("blocking") or []
            checks.append(Check("readyz", resp.status_code == 200 and not blocking,
                                f"status={body.get('status')} blocking={blocking}",
                                extra={"checks": body.get("checks", {})}))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("readyz", False, type(exc).__name__))

        # 3. 首页与静态资源
        try:
            resp = _get(client, "/")
            html = resp.text
            ok = resp.status_code == 200 and ("墨舟" in html or settings.app_name in html)
            checks.append(Check("home_page", ok, f"{resp.status_code} {len(html)} bytes"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("home_page", False, type(exc).__name__))
        try:
            resp = _get(client, "/static/css/pages.css")
            checks.append(Check("static_pages_css", resp.status_code == 200 and len(resp.text) > 1000,
                                f"{resp.status_code} {len(resp.text)} bytes"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("static_pages_css", False, type(exc).__name__))

        # 4. 书目与详情
        first_book_id = None
        try:
            resp = _get(client, "/api/v1/books", params={"page": 1, "page_size": 3})
            data = _data(resp) or {}
            items = data.get("items") or []
            first_book_id = items[0]["id"] if items else None
            checks.append(Check("books_list", bool(items), f"total={data.get('total')} items={len(items)}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("books_list", False, type(exc).__name__))
        if first_book_id:
            try:
                data = _data(_get(client, f"/api/v1/books/{first_book_id}")) or {}
                checks.append(Check("book_detail", bool(data.get("title")), f"id={first_book_id} title={data.get('title')}"))
            except Exception as exc:  # noqa: BLE001
                checks.append(Check("book_detail", False, type(exc).__name__))

        # 5. 管理员闭环（登录 → 概览 → 评测列表）
        if settings.admin_password:
            try:
                data = _data(client.post("/api/v1/auth/login",
                                         json={"email": settings.admin_email,
                                               "password": settings.admin_password})) or {}
                token = data.get("access_token", "")
                headers = {"Authorization": f"Bearer {token}"}
                overview = _data(client.get("/api/v1/admin/overview", headers=headers)) or {}
                runs = _data(client.get("/api/v1/eval/runs", headers=headers)) or []
                checks.append(Check("admin_console", bool(token) and "users" in overview,
                                    f"users={overview.get('users', {}).get('total')} eval_runs={len(runs)}"))
            except Exception as exc:  # noqa: BLE001
                checks.append(Check("admin_console", False, type(exc).__name__, required=False))
        else:
            checks.append(Check("admin_console", True, "skipped: ADMIN_PASSWORD 未配置", required=False))

        # 6. 可选：真实 RAG 问答（在线 Embedding/Rerank/LLM）
        if with_llm:
            try:
                data = _data(client.post("/api/v1/rag/ask",
                                         json={"query": "《小王子》主要讲了什么？", "top_k": 12,
                                               "rerank_top_n": 5})) or {}
                answer = (data.get("answer") or "").strip()
                cites = data.get("citations") or []
                checks.append(Check("rag_ask", bool(answer), f"answer={len(answer)}字 citations={len(cites)}"))
            except Exception as exc:  # noqa: BLE001
                checks.append(Check("rag_ask", False, type(exc).__name__))
        else:
            checks.append(Check("rag_ask", True, "skipped: 加 --with-llm 开启", required=False))
    return checks


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="墨舟 BookMate 烟雾测试")
    parser.add_argument("--base-url", default=settings.server_public_url or "http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--with-llm", action="store_true", help="额外调用一次在线 RAG 问答")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于流水线消费")
    args = parser.parse_args()

    checks = run(args.base_url, args.timeout, args.with_llm)
    failed = [c for c in checks if c.required and not c.ok]
    if args.json:
        print(json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=2))
    else:
        print(f"smoke base_url={args.base_url}")
        for check in checks:
            mark = "PASS" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"  [{mark}] {check.name:<18} {check.detail}")
        print(f"smoke {'PASSED' if not failed else 'FAILED'}: {len(checks) - len(failed)}/{len(checks)} required ok")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
