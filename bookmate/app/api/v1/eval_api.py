"""评测接口：触发 RAGAS 评测、查看运行/用例/调优建议、确认采纳。

评测耗时较长（在线模型打分），POST /eval/runs 在后台任务中执行，
立即返回 run_id；前端轮询 GET /eval/runs/{id} 等待 status 变为 done/partial/failed。
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, AdminUser
from app.core.errors import bad_request, not_found, ok
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.eval import EvalAdjustment, EvalCase, EvalRun, PromptVersion
from app.services.eval.dataset import load_dataset
from app.services.eval.report import THRESHOLDS, from_scaled
from app.services.eval.runner import EvalOptions, case_to_dict, run_eval
from app.services.llm.prompts import RAG_SYSTEM
from app.utils.limiter import enforce
from app.utils.timeutil import iso_utc, now_utc

logger = get_logger(__name__)
router = APIRouter(prefix="/eval", tags=["评测"])


class EvalRunIn(BaseModel):
    dataset: str = Field(default="eval/datasets/golden_v1.jsonl", max_length=255)
    run_name: str | None = Field(default=None, max_length=80)
    limit: int | None = Field(default=None, ge=1, le=200)
    top_k: int = Field(default=20, ge=1, le=50)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    baseline_run_id: int | None = None
    adjust: bool = True


class AdjustmentPatch(BaseModel):
    applied: bool = True
    applied_note: str | None = Field(default=None, max_length=2000)


def _run_out(run: EvalRun) -> dict:
    return {
        "id": run.id,
        "run_name": run.run_name,
        "dataset": run.dataset,
        "case_count": run.case_count,
        "status": run.status,
        "metrics": run.metrics or {},
        "baseline_run_id": run.baseline_run_id,
        "delta": run.delta or {},
        "report_dir": run.report_dir,
        "detail_csv": run.detail_csv,
        "summary_csv": run.summary_csv,
        "error": run.error,
        "created_at": iso_utc(run.created_at),
        "finished_at": iso_utc(run.finished_at),
    }


def _adjustment_out(row: EvalAdjustment) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "category": row.category,
        "finding": row.finding,
        "recommendation": row.recommendation,
        "evidence_cases": row.evidence_cases or [],
        "priority": row.priority,
        "applied": row.applied,
        "applied_note": row.applied_note,
        "created_at": iso_utc(row.created_at),
    }


async def _run_in_background(options: EvalOptions, run_id: int) -> None:
    try:
        await run_eval(options)
    except Exception as exc:  # noqa: BLE001 - 后台任务失败要落库供前端展示
        logger.warning("eval_background_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        async with SessionLocal() as db:
            run = await db.get(EvalRun, run_id)
            if run:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                run.finished_at = now_utc()
                await db.commit()


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: EvalRunIn,
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
):
    if admin.role != "admin":
        raise not_found()
    await enforce(request, "eval_run", limit=3, window_seconds=600)
    try:
        load_dataset(body.dataset)
    except Exception as exc:  # noqa: BLE001 - 数据集校验失败要回传可读信息
        raise bad_request("EVAL_DATASET_INVALID", str(exc)[:300]) from exc
    run_name = (body.run_name or f"ragas_{now_utc().strftime('%Y%m%d_%H%M%S')}")[:80]
    async with SessionLocal() as db:
        exists = await db.scalar(select(EvalRun).where(EvalRun.run_name == run_name))
        if exists:
            raise bad_request("EVAL_RUN_EXISTS", "同名运行已存在，请换一个 run_name")
        run = EvalRun(run_name=run_name, dataset=body.dataset, case_count=0, status="queued")
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
    options = EvalOptions(
        dataset=body.dataset,
        run_name=run_name,
        limit=body.limit,
        top_k=body.top_k,
        rerank_top_n=body.rerank_top_n,
        baseline_run_id=body.baseline_run_id,
        adjust=body.adjust,
        run_id=run_id,
    )
    background.add_task(_run_in_background, options, run_id)
    return ok({"id": run_id, "run_name": run_name, "status": "queued"},
              status_code=status.HTTP_202_ACCEPTED)


@router.get("/runs")
async def list_runs(admin: AdminUser, db: DB, limit: Annotated[int, Query(ge=1, le=50)] = 20):
    rows = (await db.execute(select(EvalRun).order_by(EvalRun.id.desc()).limit(limit))).scalars().all()
    return ok([_run_out(row) for row in rows])


@router.get("/runs/{run_id}")
async def get_run(run_id: int, admin: AdminUser, db: DB):
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise not_found("评测运行不存在")
    return ok(_run_out(run))


@router.get("/runs/{run_id}/cases")
async def run_cases(run_id: int, admin: AdminUser, db: DB):
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise not_found("评测运行不存在")
    rows = (
        await db.execute(select(EvalCase).where(EvalCase.run_id == run_id).order_by(EvalCase.id))
    ).scalars().all()
    return ok([case_to_dict(row) for row in rows])


@router.get("/runs/{run_id}/adjustments")
async def run_adjustments(run_id: int, admin: AdminUser, db: DB):
    rows = (
        await db.execute(
            select(EvalAdjustment).where(EvalAdjustment.run_id == run_id).order_by(EvalAdjustment.id)
        )
    ).scalars().all()
    return ok([_adjustment_out(row) for row in rows])


@router.patch("/adjustments/{adjustment_id}")
async def mark_adjustment(adjustment_id: int, body: AdjustmentPatch, admin: AdminUser, db: DB):
    row = await db.get(EvalAdjustment, adjustment_id)
    if row is None:
        raise not_found("调优建议不存在")
    row.applied = body.applied
    row.applied_note = body.applied_note
    await db.commit()
    await db.refresh(row)
    return ok(_adjustment_out(row))


@router.get("/thresholds")
async def thresholds():
    return ok({"thresholds": THRESHOLDS})


@router.get("/prompts")
async def prompt_versions(admin: AdminUser, db: DB):
    rows = (
        await db.execute(select(PromptVersion).order_by(PromptVersion.name, PromptVersion.id.desc()))
    ).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "name": row.name,
                "version": row.version,
                "description": row.description,
                "active": row.active,
                "eval_score": from_scaled(row.eval_score),
            }
            for row in rows
        ]
    )


@router.get("/prompts/current")
async def current_prompt():
    return ok({"name": "rag_system", "content": RAG_SYSTEM})


@router.get("/runs/{run_id}/export")
async def export_run(run_id: int, admin: AdminUser, db: DB, kind: Annotated[str, Query(pattern="^(detail|summary|json)$")] = "detail"):
    """下载评测产物（CSV / JSON），仅管理员可用。"""
    from pathlib import Path

    from fastapi.responses import FileResponse

    run = await db.get(EvalRun, run_id)
    if run is None:
        raise not_found("评测运行不存在")
    rel = {"detail": run.detail_csv, "summary": run.summary_csv, "json": (run.report_dir or "") + "/run.json"}[kind]
    if not rel:
        raise not_found("该运行还没有导出文件")
    root = Path(__file__).resolve().parents[3]
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        raise not_found("导出文件不存在")
    media = "text/csv; charset=utf-8" if kind != "json" else "application/json"
    return FileResponse(path, media_type=media, filename=path.name)


async def wait_run(run_id: int, timeout: float = 1800) -> dict | None:
    """脚本用：轮询运行状态直至结束。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with SessionLocal() as db:
            run = await db.get(EvalRun, run_id)
            if run and run.status in ("done", "partial", "failed"):
                return _run_out(run)
        await asyncio.sleep(2)
    return None
