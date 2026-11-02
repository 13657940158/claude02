"""RAGAS 指标计算（在线模型；仅评测时加载，主服务启动不依赖）。

- 使用 RAGAS 0.2 的 SingleTurnSample 列名：user_input / response /
  retrieved_contexts / reference；
- LLM 走在线 Qwen，Embedding 走在线 bge-m3，均通过 langchain-openai 适配；
- 无检索结果（拒答/护栏）的用例不送 RAGAS，指标保持为空并在报告中说明；
- RAGAS 不可用或调用失败时返回错误信息，不阻断问答结果落库与 CSV 导出。
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

RAGAS_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def _build_eval_dataset(samples: list[dict[str, Any]]) -> Any:
    from ragas import EvaluationDataset

    return EvaluationDataset.from_list(samples)


def _compute_sync(samples: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    from app.core.config import get_settings

    settings = get_settings()
    # 在线 API 的 TPM 限制较紧：串行执行，重试放宽，避免并发触发 429 后产生 NaN。
    run_config = RunConfig(
        timeout=int(settings.llm_timeout_seconds) + 120,
        max_retries=8,
        max_wait=120,
        max_workers=1,
    )
    llm = ChatOpenAI(
        model=settings.llm_judge_model or settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.0,
        max_tokens=2048,
    )
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key or settings.llm_api_key,
        base_url=settings.embedding_base_url,
        check_embedding_ctx_length=False,
        tiktoken_enabled=False,
    )
    result = evaluate(
        _build_eval_dataset(samples),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=LangchainLLMWrapper(llm, run_config=run_config),
        embeddings=LangchainEmbeddingsWrapper(embeddings, run_config=run_config),
        run_config=run_config,
        raise_exceptions=False,
        show_progress=False,
    )
    frame = result.to_pandas()
    rows: list[dict[str, float | None]] = []
    for _, row in frame.iterrows():
        entry: dict[str, float | None] = {}
        for key in RAGAS_METRICS:
            try:
                number = float(row.get(key))
            except (TypeError, ValueError):
                number = math.nan
            entry[key] = None if math.isnan(number) else round(number, 4)
        rows.append(entry)
    return rows


async def compute_metrics(
    cases: list[dict[str, Any]],
) -> tuple[list[dict[str, float | None]], str]:
    """cases 需含 question / answer / contexts / ground_truth；返回与输入等长的指标列表。"""
    positions = [
        index
        for index, case in enumerate(cases)
        if (case.get("contexts") or []) and (case.get("answer") or "").strip()
    ]
    metrics: list[dict[str, float | None]] = [{} for _ in cases]
    if not positions:
        return metrics, ""
    samples = [
        {
            "user_input": cases[index]["question"],
            "response": cases[index]["answer"],
            "retrieved_contexts": [text for text in cases[index]["contexts"] if text],
            "reference": cases[index]["ground_truth"],
        }
        for index in positions
    ]
    try:
        rows = await asyncio.to_thread(_compute_sync, samples)
    except Exception as exc:  # noqa: BLE001 - 评测失败不阻断主流程
        logger.warning("ragas_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        return metrics, f"{type(exc).__name__}: {str(exc)[:300]}"
    for index, row in zip(positions, rows, strict=False):
        metrics[index] = row
    return metrics, ""
