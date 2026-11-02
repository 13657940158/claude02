"""RAG 问答：检索 → 护栏 → 提示词 → 生成 → 引用校验 → 降级。"""
from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.client import get_llm_client
from app.services.llm.guardrails import (
    GuardrailResult,
    check_input,
    looks_like_refusal,
    strip_invalid_citations,
    validate_citations,
)
from app.services.llm.prompts import PROMPT_VERSION, RAG_SYSTEM, rag_user_prompt
from app.services.rag.retrieval import RetrievalResult, retrieve

logger = get_logger(__name__)

NO_EVIDENCE_ANSWER = (
    "资料里没有找到能直接回答这个问题的内容。"
    "我可以帮你换一个问法，或根据你的目标推荐一本更相关的书。"
)

# 句子末尾（或句内 40 字内）出现的引用编号，如 [1]、[1][2]、[1, 2]
_CITATION_TAIL = re.compile(r"\[\d+(?:\s*[,，]\s*\d+)*\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def citation_coverage(text: str) -> tuple[int, int]:
    """返回 (带引用标记的句子数, 有效句子数)。用于「引用覆盖率」质检，不做硬性拦截。"""
    sentences = [part for part in _SENTENCE_SPLIT.split(text or "") if len(part.strip()) >= 6]
    if not sentences:
        return 0, 0
    cited = sum(1 for sentence in sentences if _CITATION_TAIL.search(sentence))
    return cited, len(sentences)


def _check_coverage(answer: RAGAnswer) -> None:
    """长回答中超过一半句子没有引用支撑时标降级，供评测与前端提示。"""
    cited, total = citation_coverage(answer.answer)
    answer.cited_sentences = cited
    answer.sentence_count = total
    if total >= 3 and cited * 2 < total:
        answer.degraded = True


@dataclass
class RAGAnswer:
    question: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)
    blocked: bool = False
    degraded: bool = False
    grounded: bool = False
    retrieval: RetrievalResult | None = None
    prompt_version: str = PROMPT_VERSION
    model: str = ""
    latency_ms: int = 0
    cited_sentences: int = 0
    sentence_count: int = 0

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": self.citations,
            "blocked": self.blocked,
            "degraded": self.degraded,
            "grounded": self.grounded,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "citation_coverage": round(self.cited_sentences / self.sentence_count, 3)
            if self.sentence_count else None,
            "retrieval": {
                "dense": self.retrieval.dense_count if self.retrieval else 0,
                "sparse": self.retrieval.sparse_count if self.retrieval else 0,
                "kept": len(self.retrieval.chunks) if self.retrieval else 0,
                "reranked": self.retrieval.reranked if self.retrieval else False,
                "degraded": self.retrieval.degraded if self.retrieval else False,
                "elapsed_ms": self.retrieval.elapsed_ms if self.retrieval else 0,
            },
        }


def blocked_answer(question: str, guard: GuardrailResult) -> RAGAnswer:
    return RAGAnswer(
        question=question,
        answer=guard.reason,
        blocked=True,
        grounded=False,
        degraded=True,
    )


def _citation_subset(citations: list[dict], ids: list[int]) -> list[dict]:
    if not ids:
        return []
    by_index = {item["index"]: item for item in citations}
    return [by_index[i] for i in ids if i in by_index]


async def answer_question(
    db: AsyncSession,
    question: str,
    *,
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
    history_summary: str = "",
    top_k: int = 20,
    rerank_top_n: int | None = None,
    use_rerank: bool | None = None,
    mock_text: str | None = None,
) -> RAGAnswer:
    started = time.perf_counter()
    question = (question or "").strip()
    guard = check_input(question)
    if not guard.ok:
        return blocked_answer(question, guard)

    retrieval = await retrieve(
        db,
        question,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        book_ids=book_ids,
        chapter_max=chapter_max,
        use_rerank=use_rerank,
    )
    answer = RAGAnswer(
        question=question,
        answer=NO_EVIDENCE_ANSWER,
        retrieval=retrieval,
        degraded=retrieval.degraded,
        contexts=retrieval.contexts(),
    )
    if not retrieval.chunks:
        answer.latency_ms = int((time.perf_counter() - started) * 1000)
        return answer

    settings = get_settings()
    messages = [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user", "content": rag_user_prompt(question, retrieval.contexts(), history_summary)},
    ]
    try:
        text = await get_llm_client().chat(
            messages,
            temperature=0.3,
            max_tokens=800,
            mock_text=mock_text,
            operation="rag_answer",
        )
    except Exception as exc:  # noqa: BLE001 - 在线模型失败时给抽取式兜底
        logger.warning("rag_generate_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        answer.answer = _extractive_fallback(retrieval)
        answer.citations = retrieval.citations()
        answer.degraded = True
        answer.grounded = True
        answer.latency_ms = int((time.perf_counter() - started) * 1000)
        return answer

    valid, ids = validate_citations(text, len(retrieval.chunks))
    if not valid:
        text = strip_invalid_citations(text, len(retrieval.chunks))
        ids = [i for i in ids if 1 <= i <= len(retrieval.chunks)]
        answer.degraded = True
    answer.answer = text.strip()
    answer.citations = _citation_subset(retrieval.citations(), ids)
    answer.grounded = bool(answer.citations) and not looks_like_refusal(answer.answer)
    answer.model = settings.llm_model if not get_llm_client().mock else "mock"
    answer.latency_ms = int((time.perf_counter() - started) * 1000)
    _check_coverage(answer)
    return answer


def _extractive_fallback(retrieval: RetrievalResult) -> str:
    if not retrieval.chunks:
        return NO_EVIDENCE_ANSWER
    lines = ["在线回答服务暂时不可用，先给你几条原文线索："]
    for chunk in retrieval.chunks[:3]:
        snippet = chunk.content.strip().replace("\n", " ")[:110]
        lines.append(f"[{chunk.citation_index}] 《{chunk.book_title}》第{chunk.chapter_no}章：{snippet}…")
    lines.append("稍后可以再问我一次，我会给出完整解释。")
    return "\n".join(lines)


async def stream_answer(
    db: AsyncSession,
    question: str,
    *,
    book_ids: list[int] | None = None,
    chapter_max: int | None = None,
    history_summary: str = "",
) -> AsyncIterator[dict]:
    """流式版本：先返回 meta（引用），再逐段返回 delta，最后 done。

    生成过程中不做逐字引用校验（无法回溯），结束时统一校验并在 done 里给出最终引用。
    """
    guard = check_input(question)
    if not guard.ok:
        yield {"type": "blocked", "message": guard.reason}
        return
    retrieval = await retrieve(db, question, book_ids=book_ids, chapter_max=chapter_max)
    if not retrieval.chunks:
        yield {"type": "meta", "citations": [], "degraded": retrieval.degraded}
        yield {"type": "delta", "text": NO_EVIDENCE_ANSWER}
        yield {"type": "done", "answer": NO_EVIDENCE_ANSWER, "citations": []}
        return
    yield {
        "type": "meta",
        "citations": retrieval.citations(),
        "degraded": retrieval.degraded,
        "retrieval": {"dense": retrieval.dense_count, "sparse": retrieval.sparse_count,
                      "kept": len(retrieval.chunks), "reranked": retrieval.reranked},
    }
    messages = [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user", "content": rag_user_prompt(question, retrieval.contexts(), history_summary)},
    ]
    parts: list[str] = []
    try:
        async for delta in get_llm_client().stream_chat(messages, temperature=0.3, max_tokens=800,
                                                        operation="rag_stream"):
            parts.append(delta)
            yield {"type": "delta", "text": delta}
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag_stream_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        fallback = _extractive_fallback(retrieval)
        yield {"type": "delta", "text": fallback}
        yield {"type": "done", "answer": fallback, "citations": retrieval.citations(), "degraded": True}
        return
    full = "".join(parts).strip()
    valid, ids = validate_citations(full, len(retrieval.chunks))
    if not valid:
        full = strip_invalid_citations(full, len(retrieval.chunks))
        ids = [i for i in ids if 1 <= i <= len(retrieval.chunks)]
    cited, total = citation_coverage(full)
    low_coverage = total >= 3 and cited * 2 < total
    yield {"type": "done", "answer": full, "citations": _citation_subset(retrieval.citations(), ids),
           "degraded": retrieval.degraded or not valid or low_coverage,
           "citation_coverage": round(cited / total, 3) if total else None}
