#!/usr/bin/env python3
"""幂等创建管理员账号。

用法（容器内）：
  python scripts/create_admin.py
  python scripts/create_admin.py --email admin@example.com --password 'NewPass123'
不传参数时读取 ADMIN_EMAIL / ADMIN_PASSWORD。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.auth import ensure_admin
from scripts._common import run_main


async def _run(email: str, password: str) -> int:
    async with SessionLocal() as db:
        user, created = await ensure_admin(db, email, password)
        if created:
            print(f"[admin] 已创建：{user.email}（id={user.id}）")
        else:
            print(f"[admin] 已存在：{user.email}（id={user.id}，role={user.role}）")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="创建/提升管理员")
    parser.add_argument("--email", default=settings.admin_email)
    parser.add_argument("--password", default=settings.admin_password)
    args = parser.parse_args()
    if not args.password:
        print("[admin] 缺少密码：请在 .env 设置 ADMIN_PASSWORD 或传 --password", file=sys.stderr)
        return 1
    if len(args.password) < 8:
        print("[admin] 密码至少 8 位", file=sys.stderr)
        return 1
    return run_main(lambda: _run(args.email.strip().lower(), args.password))


if __name__ == "__main__":
    sys.exit(main())
