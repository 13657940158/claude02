"""评测数据集加载与校验。

数据集为 JSONL，一行一个用例：
{
  "case_key": "ly-001",              # 运行内唯一，建议 {书}-{序号}
  "book": "论语",                    # 用于限定检索书目；可空表示全库检索
  "category": "definition",          # 便于分桶统计
  "question": "...",                 # 用户问题
  "ground_truth": "...",             # 参考答案，供 context_recall/precision 使用
  "reference_contexts": ["..."],     # 人工标注的相关原文，便于审计（可选）
  "expected_chapters": ["学而第一"]   # 期望命中的章节（用于额外命中率统计，可选）
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import AppError

REQUIRED_FIELDS = ("case_key", "question", "ground_truth")


@dataclass
class EvalCaseSpec:
    case_key: str
    question: str
    ground_truth: str
    book: str = ""
    category: str = "general"
    reference_contexts: list[str] = field(default_factory=list)
    expected_chapters: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def load_dataset(path: str | Path) -> list[EvalCaseSpec]:
    file = Path(path)
    if not file.exists():
        raise AppError(code="EVAL_DATASET_MISSING", status_code=404, message=f"评测集不存在：{path}")
    cases: list[EvalCaseSpec] = []
    seen: set[str] = set()
    with file.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppError(
                    code="EVAL_DATASET_INVALID",
                    status_code=422,
                    message=f"第 {line_no} 行不是合法 JSON：{exc.msg}",
                ) from exc
            missing = [name for name in REQUIRED_FIELDS if not str(item.get(name) or "").strip()]
            if missing:
                raise AppError(
                    code="EVAL_DATASET_INVALID",
                    status_code=422,
                    message=f"第 {line_no} 行缺少字段：{', '.join(missing)}",
                )
            key = str(item["case_key"]).strip()
            if key in seen:
                raise AppError(
                    code="EVAL_DATASET_INVALID",
                    status_code=422,
                    message=f"case_key 重复：{key}",
                )
            seen.add(key)
            cases.append(
                EvalCaseSpec(
                    case_key=key,
                    question=str(item["question"]).strip(),
                    ground_truth=str(item["ground_truth"]).strip(),
                    book=str(item.get("book") or "").strip(),
                    category=str(item.get("category") or "general").strip(),
                    reference_contexts=[str(x) for x in (item.get("reference_contexts") or []) if str(x).strip()],
                    expected_chapters=[str(x) for x in (item.get("expected_chapters") or []) if str(x).strip()],
                    raw=item,
                )
            )
    if not cases:
        raise AppError(code="EVAL_DATASET_EMPTY", status_code=422, message="评测集为空")
    return cases


def chapter_hit_rate(chunks: list[dict], expected_chapters: list[str]) -> float | None:
    """额外指标：期望章节是否出现在最终检索块中。无标注时返回 None。"""
    expected = {title.strip() for title in expected_chapters if title.strip()}
    if not expected:
        return None
    hit = {
        str(chunk.get("chapter_title") or "").strip()
        for chunk in chunks
        if str(chunk.get("chapter_title") or "").strip() in expected
    }
    return len(hit) / len(expected)
