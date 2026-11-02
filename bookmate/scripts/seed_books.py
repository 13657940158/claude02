#!/usr/bin/env python3
"""导入内置 12 本书目（幂等）。

用法：python scripts/seed_books.py --count 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.scripts.seed_books import seed_books
from scripts._common import run_main


async def _run(count: int | None) -> int:
    async with SessionLocal() as db:
        created, total = await seed_books(db, count)
        print(f"[seed] 新增 {created} 本，当前书目共 {total} 本")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="导入内置书目")
    parser.add_argument("--count", type=int, default=settings.seed_book_count)
    args = parser.parse_args()
    return run_main(lambda: _run(args.count))


if __name__ == "__main__":
    sys.exit(main())
