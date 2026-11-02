"""在容器或本机执行 RAGAS 评测。

示例：
    python scripts/run_eval.py --limit 3 --run-name smoke
    python scripts/run_eval.py --baseline-run-id 1 --run-name ragas_after_prompt_v2
    docker compose ... exec api python scripts/run_eval.py --limit 3

注意：评测会实际调用在线 LLM/Embedding，20 用例约需数分钟；先用 --limit 验证链路。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.eval.runner import EvalOptions, run_eval  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="墨舟 BookMate RAGAS 评测")
    parser.add_argument("--dataset", default="eval/datasets/golden_v1.jsonl", help="JSONL 评测集路径")
    parser.add_argument("--run-name", default=None, help="运行名（唯一）；默认按时间生成")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条，便于快速验证")
    parser.add_argument("--top-k", type=int, default=20, help="检索候选数")
    parser.add_argument("--rerank-top-n", type=int, default=None, help="重排保留数")
    parser.add_argument("--baseline-run-id", type=int, default=None, help="对比的基线运行 ID")
    parser.add_argument("--no-adjust", action="store_true", help="不生成 LLM 调优建议")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    result = await run_eval(
        EvalOptions(
            dataset=args.dataset,
            run_name=args.run_name,
            limit=args.limit,
            top_k=args.top_k,
            rerank_top_n=args.rerank_top_n,
            baseline_run_id=args.baseline_run_id,
            adjust=not args.no_adjust,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("done", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
