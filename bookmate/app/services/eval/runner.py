"""RAGAS 评测主流程：取金标集 → 跑 RAG → RAGAS 打分 → CSV → LLM 调优建议。

设计约束：
- 评测独立于 Web 请求，使用独立 AsyncSession，避免污染在线连接；
- 单个用例失败不终止整轮评测，错误写入用例与运行记录；
- 首轮没有 baseline，后续同数据集运行自动对比上一轮，delta 写入 EvalRun；
- 调优建议只生成、不自动改线上配置；人工采纳后用 applied/applied_note 记录。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.book import Book
from app.models.eval import EvalAdjustment, EvalCase, EvalRun, PromptVersion
from app.services.eval.dataset import EvalCaseSpec, chapter_hit_rate, load_dataset
from app.services.eval.ragas_runner import compute_metrics
from app.services.eval.report import (
    ADJUSTMENT_ROOT,
    REPORT_ROOT,
    classify_failure,
    from_scaled,
    metric_delta,
    overall_score,
    summarize,
    to_scaled,
    write_adjustment_report,
    write_detail_csv,
    write_run_json,
    write_summary_csv,
)
from app.services.llm.client import get_llm_client
from app.services.llm.prompts import (
    EVAL_ADJUST_SYSTEM,
    PROMPT_VERSION,
    RAG_SYSTEM,
    eval_adjust_user_prompt,
)
from app.services.rag.answer import answer_question
from app.utils.text import estimate_tokens
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

DEFAULT_DATASET = "eval/datasets/golden_v1.jsonl"
ANSWER_CONCURRENCY = 3


@dataclass
class EvalOptions:
    dataset: str = DEFAULT_DATASET
    run_name: str | None = None
    limit: int | None = None
    top_k: int = 20
    rerank_top_n: int | None = None
    baseline_run_id: int | None = None
    adjust: bool = True
    # 由 API 预先创建 queued 记录后传入，避免同名 run_name 重复插入
    run_id: int | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_project_root()))
    except ValueError:
        return str(path)


async def _resolve_books(db: AsyncSession, cases: list[EvalCaseSpec]) -> dict[str, int]:
    titles = {case.book for case in cases if case.book}
    if not titles:
        return {}
    rows = (await db.execute(select(Book).where(Book.title.in_(titles)))).scalars().all()
    return {book.title: book.id for book in rows}


async def _answer_case(
    db: AsyncSession,
    case: EvalCaseSpec,
    book_id: int | None,
    options: EvalOptions,
) -> dict[str, Any]:
    try:
        result = await answer_question(
            db,
            case.question,
            book_ids=[book_id] if book_id else None,
            top_k=options.top_k,
            rerank_top_n=options.rerank_top_n,
        )
    except Exception as exc:  # noqa: BLE001 - 单用例失败不终止整轮
        logger.warning(
            "eval_case_failed",
            extra={"extra_fields": {"case_key": case.case_key, "error": type(exc).__name__}},
        )
        return {
            "case_key": case.case_key,
            "book": case.book,
            "category": case.category,
            "question": case.question,
            "ground_truth": case.ground_truth,
            "reference_contexts": case.reference_contexts,
            "expected_chapters": case.expected_chapters,
            "answer": "",
            "contexts": [],
            "refs": [],
            "degraded": True,
            "blocked": False,
            "latency_ms": 0,
            "chapter_hit_rate": None,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    chunks = result.retrieval.chunks if result.retrieval else []
    chunk_dicts = [
        {"chapter_title": chunk.chapter_title, "chapter_no": chunk.chapter_no} for chunk in chunks
    ]
    return {
        "case_key": case.case_key,
        "book": case.book,
        "category": case.category,
        "question": case.question,
        "ground_truth": case.ground_truth,
        "reference_contexts": case.reference_contexts,
        "expected_chapters": case.expected_chapters,
        "answer": result.answer,
        "contexts": result.contexts,
        "refs": result.citations,
        "degraded": result.degraded,
        "blocked": result.blocked,
        "latency_ms": result.latency_ms,
        "chapter_hit_rate": chapter_hit_rate(chunk_dicts, case.expected_chapters),
        "error": "",
    }


async def _answer_all(cases: list[EvalCaseSpec], options: EvalOptions) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        books = await _resolve_books(db, cases)

    semaphore = asyncio.Semaphore(ANSWER_CONCURRENCY)
    results: list[dict[str, Any] | None] = [None] * len(cases)

    async def run_one(index: int, case: EvalCaseSpec) -> None:
        async with semaphore:
            async with SessionLocal() as session:
                results[index] = await _answer_case(
                    session, case, books.get(case.book), options
                )
            if index % 5 == 0:
                logger.info("eval_progress", extra={"extra_fields": {"done": index + 1, "total": len(cases)}})

    await asyncio.gather(*(run_one(index, case) for index, case in enumerate(cases)))
    return [item for item in results if item is not None]


async def _pick_baseline(db: AsyncSession, run: EvalRun, options: EvalOptions) -> EvalRun | None:
    if options.baseline_run_id:
        return await db.get(EvalRun, options.baseline_run_id)
    return await db.scalar(
        select(EvalRun)
        .where(EvalRun.dataset == run.dataset, EvalRun.id < run.id, EvalRun.status.in_(("done", "partial")))
        .order_by(EvalRun.id.desc())
        .limit(1)
    )


async def _persist_cases(db: AsyncSession, run: EvalRun, cases: list[dict[str, Any]]) -> None:
    for case in cases:
        metrics = case.get("metrics") or {}
        db.add(
            EvalCase(
                run_id=run.id,
                case_key=case["case_key"],
                question=case["question"],
                answer=case.get("answer") or "",
                ground_truth=case.get("ground_truth") or "",
                contexts=case.get("contexts") or [],
                refs=case.get("refs") or [],
                faithfulness=to_scaled(metrics.get("faithfulness")),
                answer_relevancy=to_scaled(metrics.get("answer_relevancy")),
                context_precision=to_scaled(metrics.get("context_precision")),
                context_recall=to_scaled(metrics.get("context_recall")),
                failure_type=case.get("failure_type"),
                latency_ms=int(case.get("latency_ms") or 0),
                cost_tokens=estimate_tokens(
                    (case.get("answer") or "")[:4000] + "\n".join(case.get("contexts") or [])[:8000]
                ),
                category=(case.get("category") or "")[:32],
                chapter_hit_rate=to_scaled(case.get("chapter_hit_rate")),
                citation_count=len(case.get("refs") or []),
            )
        )
    await db.commit()


def _fallback_adjustments(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在线 LLM 不可用时的确定性建议，保证调优报告仍可产出。"""
    advice = {
        "retrieval_missing": ("稀疏召回或分片不足", "扩大 top_k，补充章节关键词/书名别名的稀疏检索，检查该章节是否已入库。"),
        "retrieval_noise": ("召回上下文噪声高", "提高 rerank 保留阈值、压缩上下文条数，并要求模型只采用直接相关的段落。"),
        "hallucination": ("回答超出资料", "强化提示词的「只依据资料」约束，对未获引用支撑的句子做二次校验。"),
        "off_topic": ("答案未对准问题", "在提示词中先复述问题要点，再给结论；对短问题补充意图澄清。"),
        "no_answer": ("有检索但无回答/拒答", "检查无证据兜底与护栏是否误触发，必要时增加查询改写。"),
        "guardrail": ("护栏误触发", "复核该问题的正则与敏感词规则，加入白名单回归用例。"),
        "degraded": ("依赖降级", "检查在线模型/向量库/重排的可用性与超时配置。"),
    }
    grouped: dict[str, list[str]] = {}
    for failure in failures:
        grouped.setdefault(failure.get("failure_type") or "retrieval_missing", []).append(failure["case_key"])
    output = []
    for failure_type, keys in grouped.items():
        finding, recommendation = advice.get(failure_type, ("综合质量问题", "逐条复现失败用例后再调整。"))
        output.append({
            "category": failure_type,
            "finding": finding,
            "recommendation": recommendation,
            "evidence_cases": keys[:6],
            "priority": "P0" if failure_type in ("hallucination", "no_answer") else "P1",
        })
    return output


async def _generate_adjustments(
    run: EvalRun,
    summary: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    enabled: bool,
) -> tuple[list[dict[str, Any]], str]:
    failures = [
        {
            "case_key": case["case_key"],
            "question": case["question"],
            "answer": (case.get("answer") or "")[:300],
            "failure_type": case.get("failure_type"),
            **{key: value for key, value in (case.get("metrics") or {}).items()},
        }
        for case in cases
        if case.get("failure_type") or any(
            value is not None and value < 0.65 for value in (case.get("metrics") or {}).values()
        )
    ]
    if not enabled:
        return [], "已禁用自动调优建议"
    if not failures:
        return [], ""
    try:
        payload = await get_llm_client().chat_json(
            [
                {"role": "system", "content": EVAL_ADJUST_SYSTEM},
                {"role": "user", "content": eval_adjust_user_prompt(summary, failures[:12])},
            ],
            temperature=0.2,
            max_tokens=1800,
            operation="eval_adjust",
            mock_payload={"adjustments": _fallback_adjustments(failures)},
        )
        items = payload.get("adjustments") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("模型未返回 adjustments")
        allowed_keys = {case["case_key"] for case in cases}
        cleaned = []
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            evidence = [str(key) for key in (item.get("evidence_cases") or []) if str(key) in allowed_keys]
            cleaned.append({
                "category": str(item.get("category") or "prompt")[:32],
                "finding": str(item.get("finding") or "")[:1000],
                "recommendation": str(item.get("recommendation") or "")[:1500],
                "evidence_cases": evidence,
                "priority": str(item.get("priority") or "P2").upper()[:4],
            })
        if cleaned:
            return cleaned, ""
        raise ValueError("调整建议为空")
    except Exception as exc:  # noqa: BLE001 - LLM 失败时用规则建议兜底
        logger.warning("eval_adjust_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        return _fallback_adjustments(failures), f"LLM 调优建议不可用，已用规则兜底：{type(exc).__name__}"


async def _sync_prompt_version(db: AsyncSession, overall: float | None) -> None:
    if overall is None:
        return
    existing = await db.scalar(
        select(PromptVersion).where(
            PromptVersion.name == "rag_system", PromptVersion.version == PROMPT_VERSION
        )
    )
    score = to_scaled(overall)
    if existing:
        existing.eval_score = score
        existing.active = True
    else:
        db.add(
            PromptVersion(
                name="rag_system",
                version=PROMPT_VERSION,
                content=RAG_SYSTEM,
                description="RAG 问答系统提示词；由评测流程同步",
                active=True,
                eval_score=score,
            )
        )
    await db.commit()


async def run_eval(options: EvalOptions | None = None) -> dict[str, Any]:
    options = options or EvalOptions()
    root = _project_root()
    dataset_path = Path(options.dataset)
    if not dataset_path.is_absolute():
        dataset_path = root / dataset_path
    specs = load_dataset(dataset_path)
    if options.limit:
        specs = specs[: options.limit]

    run_name = options.run_name or f"ragas_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    run_name = run_name[:80]
    async with SessionLocal() as db:
        run = await db.get(EvalRun, options.run_id) if options.run_id else None
        if run is None:
            run = EvalRun(
                run_name=run_name,
                dataset=_relative(dataset_path),
                case_count=len(specs),
                status="running",
                report_dir="",
            )
            db.add(run)
        else:
            run.run_name = run_name
            run.dataset = _relative(dataset_path)
            run.case_count = len(specs)
            run.status = "running"
            run.error = None
        await db.commit()
        await db.refresh(run)

    cases = await _answer_all(specs, options)

    metrics, metrics_error = await compute_metrics(cases)
    for case, metric_row in zip(cases, metrics, strict=True):
        case["metrics"] = metric_row
        case["failure_type"] = classify_failure(
            answer=case.get("answer") or "",
            metrics=metric_row,
            degraded=bool(case.get("degraded")),
            blocked=bool(case.get("blocked")),
        )

    summary = summarize(cases)
    summary["overall"] = overall_score(summary)
    expected_scored = sum(1 for case in cases if (case.get("contexts") or []) and (case.get("answer") or "").strip())
    missing_metrics = sum(
        1
        for case in cases
        if (case.get("contexts") or [])
        and (case.get("answer") or "").strip()
        and all((case.get("metrics") or {}).get(key) is None for key in (
            "faithfulness", "answer_relevancy", "context_precision", "context_recall"
        ))
    )
    summary["metrics_expected"] = expected_scored
    summary["metrics_missing"] = missing_metrics
    if metrics_error:
        summary["metrics_error"] = metrics_error
    if missing_metrics:
        note = f"{missing_metrics}/{expected_scored} 个用例的 RAGAS 指标缺失（多为限流或模型拒绝）"
        metrics_error = f"{metrics_error}; {note}" if metrics_error else note

    async with SessionLocal() as db:
        run = await db.get(EvalRun, run.id)
        assert run is not None
        baseline = await _pick_baseline(db, run, options)
        delta = metric_delta(summary, baseline.metrics or {}) if baseline else {}

        report_dir = REPORT_ROOT / run_name
        detail_path = report_dir / "detail.csv"
        summary_path = report_dir / "summary.csv"
        report_dir.mkdir(parents=True, exist_ok=True)
        write_detail_csv(detail_path, run_name, cases)
        write_summary_csv(summary_path, run_name, summary)

        adjustments, adjustment_note = await _generate_adjustments(
            run, summary, cases, enabled=options.adjust
        )
        for item in adjustments:
            db.add(
                EvalAdjustment(
                    run_id=run.id,
                    category=item["category"],
                    finding=item["finding"],
                    recommendation=item["recommendation"],
                    evidence_cases=item["evidence_cases"],
                    priority=item.get("priority") or "P2",
                )
            )
        adjustment_path = ADJUSTMENT_ROOT / f"{run_name}.md"
        failures = [case for case in cases if case.get("failure_type")]
        write_adjustment_report(
            adjustment_path,
            run_name=run_name,
            summary=summary,
            adjustments=adjustments,
            failures=failures,
        )
        write_run_json(
            report_dir / "run.json",
            {
                "run_name": run_name,
                "dataset": _relative(dataset_path),
                "summary": summary,
                "delta": delta,
                "baseline_run_id": baseline.id if baseline else None,
                "adjustment_note": adjustment_note,
                "adjustments": adjustments,
                "failures": [
                    {
                        "case_key": case["case_key"],
                        "failure_type": case.get("failure_type"),
                        "metrics": case.get("metrics"),
                    }
                    for case in failures
                ],
            },
        )

        status = "partial" if metrics_error and (metrics_error.startswith("ragas") or "缺失" in metrics_error) else "done"
        run.status = status
        run.metrics = summary
        run.baseline_run_id = baseline.id if baseline else None
        run.delta = delta
        run.report_dir = _relative(report_dir)
        run.detail_csv = _relative(detail_path)
        run.summary_csv = _relative(summary_path)
        run.error = metrics_error or adjustment_note or None
        run.finished_at = now_utc()
        await _persist_cases(db, run, cases)
        await _sync_prompt_version(db, summary.get("overall"))
        await db.commit()

    logger.info(
        "eval_run_done",
        extra={"extra_fields": {"run": run_name, "overall": summary.get("overall"), "status": status}},
    )
    return {
        "run_id": run.id,
        "run_name": run_name,
        "status": status,
        "dataset": _relative(dataset_path),
        "summary": summary,
        "delta": delta,
        "baseline_run_id": run.baseline_run_id,
        "report_dir": run.report_dir,
        "detail_csv": run.detail_csv,
        "summary_csv": run.summary_csv,
        "adjustments": adjustments,
        "adjustment_note": adjustment_note,
        "metrics_error": metrics_error,
    }


def case_to_dict(row: EvalCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "case_key": row.case_key,
        "question": row.question,
        "answer": row.answer,
        "ground_truth": row.ground_truth,
        "faithfulness": from_scaled(row.faithfulness),
        "answer_relevancy": from_scaled(row.answer_relevancy),
        "context_precision": from_scaled(row.context_precision),
        "context_recall": from_scaled(row.context_recall),
        "failure_type": row.failure_type,
        "latency_ms": row.latency_ms,
        "refs": row.refs or [],
        "context_count": len(row.contexts or []),
        "category": row.category or "",
        "chapter_hit_rate": from_scaled(row.chapter_hit_rate),
        "citation_count": row.citation_count or 0,
    }
