#!/usr/bin/env python3
"""自举 CLI：集合 → 管理员 → 种子书 → 语料入库 → 检索自检。

用途：
- 部署时作为一次性 init 步骤运行，失败返回非 0，部署脚本据此中止；
- 本地排查“为什么书进不了检索”时手动重跑，幂等，不会重复写入。

用法：
    python scripts/bootstrap_cli.py                 # 完整自举
    python scripts/bootstrap_cli.py --limit 3       # 只处理前 3 个语料文件
    python scripts/bootstrap_cli.py --trigger deploy
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.bootstrap import run_bootstrap
from scripts._common import run_main


async def _run(args: argparse.Namespace) -> int:
    result = await run_bootstrap(trigger=args.trigger, ingest_limit=args.limit)
    payload = result.as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "running_elsewhere":
        print("[bootstrap] 另一个进程正在自举，本次跳过", file=sys.stderr)
        return 0
    failed = [s for s in payload.get("stages", []) if s.get("status") == "failed"]
    if failed or payload["status"] != "done":
        print(f"[bootstrap] 失败阶段：{[s['stage'] for s in failed]}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="墨舟 BookMate 自举")
    parser.add_argument("--trigger", default="deploy", help="自举来源（记录到 BootstrapRun.trigger）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个语料文件")
    args = parser.parse_args()
    return run_main(lambda: _run(args))


if __name__ == "__main__":
    raise SystemExit(main())
