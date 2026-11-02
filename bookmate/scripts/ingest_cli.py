#!/usr/bin/env python3
"""手动入库 CLI。

用法：
  python scripts/ingest_cli.py --book-id 1
  python scripts/ingest_cli.py --file data/books/小王子-圣埃克苏佩里.txt
  python scripts/ingest_cli.py --scan            # 扫描 CORPUS_DIR 全量
  python scripts/ingest_cli.py --book-id 1 --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.rag.ingest import ingest_book_id, ingest_file, scan_and_ingest
from scripts._common import run_main


async def _run(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        if args.scan:
            reports = await scan_and_ingest(db, limit=args.limit)
            for report in reports:
                print(json.dumps(report.as_dict(), ensure_ascii=False))
            failed = [r for r in reports if r.status == "failed"]
            return 0 if not failed else 1
        if args.file:
            path = Path(args.file)
            if not path.exists():
                print(f"[ingest] 文件不存在：{path}", file=sys.stderr)
                return 1
            report = await ingest_file(db, path, force=args.force)
        elif args.book_id:
            report = await ingest_book_id(db, args.book_id, args.file, force=args.force)
        else:
            print("[ingest] 需要 --book-id / --file / --scan 之一", file=sys.stderr)
            return 1
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="语料入库")
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--file")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--corpus-dir", default=settings.corpus_dir)
    args = parser.parse_args()
    return run_main(lambda: _run(args))


if __name__ == "__main__":
    sys.exit(main())
