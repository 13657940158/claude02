"""评测结果汇总、CSV 导出与调优报告。

指标统一以 0-1 浮点保存在内存，数据库中乘 10000 存整数，避免浮点误差。
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
METRIC_LABELS = {
    "faithfulness": "忠实度",
    "answer_relevancy": "答案相关性",
    "context_precision": "上下文精确率",
    "context_recall": "上下文召回率",
}
THRESHOLDS = {
    "faithfulness": 0.65,
    "answer_relevancy": 0.65,
    "context_precision": 0.55,
    "context_recall": 0.60,
}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = PROJECT_ROOT / "eval"
REPORT_ROOT = EVAL_ROOT / "reports"
ADJUSTMENT_ROOT = EVAL_ROOT / "adjustments"


def to_scaled(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0, min(10000, int(round(number * 10000))))


def from_scaled(value: Any) -> float | None:
    if value is None:
        return None
    return round(int(value) / 10000, 4)


def classify_failure(
    *,
    answer: str,
    metrics: dict[str, float | None],
    degraded: bool = False,
    blocked: bool = False,
) -> str | None:
    """按影响优先级给出单一失败类型，便于调优归因。"""
    if blocked:
        return "guardrail"
    if not (answer or "").strip():
        return "no_answer"
    if metrics.get("faithfulness") is not None and metrics["faithfulness"] < THRESHOLDS["faithfulness"]:
        return "hallucination"
    if metrics.get("context_recall") is not None and metrics["context_recall"] < THRESHOLDS["context_recall"]:
        return "retrieval_missing"
    if metrics.get("context_precision") is not None and metrics["context_precision"] < THRESHOLDS["context_precision"]:
        return "retrieval_noise"
    if metrics.get("answer_relevancy") is not None and metrics["answer_relevancy"] < THRESHOLDS["answer_relevancy"]:
        return "off_topic"
    if degraded:
        return "degraded"
    return None


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """cases: [{category, metrics:{...}, failure_type, chapter_hit_rate, latency_ms}]."""
    summary: dict[str, Any] = {"case_count": len(cases)}
    for key in METRIC_KEYS:
        values = [case["metrics"][key] for case in cases if case.get("metrics", {}).get(key) is not None]
        summary[key] = round(sum(values) / len(values), 4) if values else None
        summary[f"{key}_count"] = len(values)
        summary[f"{key}_pass"] = sum(1 for value in values if value >= THRESHOLDS[key])
    scored = [case for case in cases if any(case.get("metrics", {}).get(k) is not None for k in METRIC_KEYS)]
    summary["scored_count"] = len(scored)
    summary["failure_count"] = sum(1 for case in cases if case.get("failure_type"))
    summary["pass_count"] = sum(1 for case in cases if scored and not case.get("failure_type"))
    summary["pass_rate"] = (
        round(summary["pass_count"] / len(scored), 4) if scored else None
    )
    hit_values = [case["chapter_hit_rate"] for case in cases if case.get("chapter_hit_rate") is not None]
    summary["chapter_hit_rate"] = round(sum(hit_values) / len(hit_values), 4) if hit_values else None
    latencies = [case["latency_ms"] for case in cases if case.get("latency_ms")]
    summary["avg_latency_ms"] = round(sum(latencies) / len(latencies)) if latencies else 0

    by_category: dict[str, dict[str, Any]] = defaultdict(dict)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case.get("category") or "general"].append(case)
    for category, rows in grouped.items():
        entry: dict[str, Any] = {"case_count": len(rows)}
        for key in METRIC_KEYS:
            values = [row["metrics"][key] for row in rows if row.get("metrics", {}).get(key) is not None]
            entry[key] = round(sum(values) / len(values), 4) if values else None
        entry["failure_count"] = sum(1 for row in rows if row.get("failure_type"))
        by_category[category] = entry
    summary["by_category"] = dict(by_category)

    failures: dict[str, int] = defaultdict(int)
    for case in cases:
        if case.get("failure_type"):
            failures[case["failure_type"]] += 1
    summary["failure_types"] = dict(failures)
    return summary


def metric_delta(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    delta: dict[str, float] = {}
    for key in METRIC_KEYS:
        cur = current.get(key)
        base = baseline.get(key)
        if cur is not None and base is not None:
            delta[key] = round(float(cur) - float(base), 4)
    return delta


def write_detail_csv(path: Path, run_name: str, cases: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "run_name", "case_key", "category", "question", "answer", "ground_truth",
            "faithfulness", "answer_relevancy", "context_precision", "context_recall",
            "chapter_hit_rate", "failure_type", "latency_ms", "context_count", "citation_count",
        ])
        for case in cases:
            metrics = case.get("metrics") or {}
            writer.writerow([
                run_name,
                case.get("case_key", ""),
                case.get("category", ""),
                case.get("question", ""),
                case.get("answer", ""),
                case.get("ground_truth", ""),
                _fmt(metrics.get("faithfulness")),
                _fmt(metrics.get("answer_relevancy")),
                _fmt(metrics.get("context_precision")),
                _fmt(metrics.get("context_recall")),
                _fmt(case.get("chapter_hit_rate")),
                case.get("failure_type") or "",
                case.get("latency_ms") or 0,
                len(case.get("contexts") or []),
                len(case.get("refs") or []),
            ])
    return str(path)


def write_summary_csv(path: Path, run_name: str, summary: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_name", "metric", "metric_label", "mean", "threshold", "pass_count", "scored_count", "pass_rate"])
        for key in METRIC_KEYS:
            scored = int(summary.get(f"{key}_count") or 0)
            passed = int(summary.get(f"{key}_pass") or 0)
            writer.writerow([
                run_name, key, METRIC_LABELS[key], _fmt(summary.get(key)), THRESHOLDS[key],
                passed, scored, round(passed / scored, 4) if scored else "",
            ])
        writer.writerow([run_name, "overall", "整体", _fmt(summary.get("overall")), "", summary.get("pass_count") or 0,
                         summary.get("scored_count") or 0, summary.get("pass_rate") or ""])
        writer.writerow([run_name, "case_count", "用例数", summary.get("case_count") or 0, "", "", "", ""])
        writer.writerow([run_name, "failure_count", "失败数", summary.get("failure_count") or 0, "", "", "", ""])
        writer.writerow([run_name, "chapter_hit_rate", "章节命中率", _fmt(summary.get("chapter_hit_rate")), "", "", "", ""])
        writer.writerow([run_name, "avg_latency_ms", "平均延迟(ms)", summary.get("avg_latency_ms") or 0, "", "", "", ""])
        writer.writerow([])
        writer.writerow(["failure_type", "count"])
        for failure, count in (summary.get("failure_types") or {}).items():
            writer.writerow([failure, count])
        writer.writerow([])
        writer.writerow(["category", *METRIC_KEYS, "case_count", "failure_count"])
        for category, entry in (summary.get("by_category") or {}).items():
            writer.writerow([category, *[_fmt(entry.get(k)) for k in METRIC_KEYS],
                             entry.get("case_count") or 0, entry.get("failure_count") or 0])
    return str(path)


def overall_score(summary: dict[str, Any]) -> float | None:
    values = [summary.get(key) for key in METRIC_KEYS if summary.get(key) is not None]
    return round(sum(values) / len(values), 4) if values else None


def write_adjustment_report(
    path: Path,
    *,
    run_name: str,
    summary: dict[str, Any],
    adjustments: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# RAGAS 调优报告 · {run_name}",
        "",
        "## 指标汇总",
        "",
        "| 指标 | 均值 | 阈值 | 通过/有效 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in METRIC_KEYS:
        mean = summary.get(key)
        lines.append(
            f"| {METRIC_LABELS[key]} (`{key}`) | {mean if mean is not None else '-'} | "
            f"{THRESHOLDS[key]} | {summary.get(f'{key}_pass', 0)}/{summary.get(f'{key}_count', 0)} |"
        )
    lines.extend([
        "",
        f"- 用例数：{summary.get('case_count', 0)}；失败：{summary.get('failure_count', 0)}；"
        f"通过率：{summary.get('pass_rate')}；章节命中率：{summary.get('chapter_hit_rate')}",
        f"- 失败类型分布：`{json.dumps(summary.get('failure_types') or {}, ensure_ascii=False)}`",
        "",
        "## 调优建议",
        "",
    ])
    if not adjustments:
        lines.append("本轮没有低于阈值的用例，暂不需要调整。")
    for index, item in enumerate(adjustments, start=1):
        lines.extend([
            f"### {index}. [{item.get('priority', 'P2')}] {item.get('category', 'general')}",
            "",
            f"- **问题**：{item.get('finding', '')}",
            f"- **建议**：{item.get('recommendation', '')}",
            f"- **证据用例**：{', '.join(item.get('evidence_cases') or []) or '无'}",
            "- **状态**：待人工确认后应用",
            "",
        ])
    if failures:
        lines.extend(["## 失败用例明细", "", "| case_key | 类型 | faithfulness | recall | precision | relevancy |", "| --- | --- | ---: | ---: | ---: | ---: |"])
        for item in failures:
            metrics = item.get("metrics") or {}
            lines.append(
                f"| {item.get('case_key')} | {item.get('failure_type') or '-'} | "
                f"{_fmt(metrics.get('faithfulness')) or '-'} | {_fmt(metrics.get('context_recall')) or '-'} | "
                f"{_fmt(metrics.get('context_precision')) or '-'} | {_fmt(metrics.get('answer_relevancy')) or '-'} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def write_run_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}"
