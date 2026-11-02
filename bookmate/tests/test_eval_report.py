"""评测汇总与归因的纯函数测试。"""
from app.services.eval.report import (
    classify_failure,
    from_scaled,
    metric_delta,
    summarize,
    to_scaled,
)


def test_scaled_roundtrip_and_bounds() -> None:
    assert to_scaled(0.764) == 7640
    assert to_scaled(1.2) == 10000
    assert to_scaled(-0.1) == 0
    assert to_scaled(None) is None
    assert to_scaled("not-a-number") is None
    assert from_scaled(7640) == 0.764
    assert from_scaled(None) is None


def test_classify_failure_priority() -> None:
    assert classify_failure(answer="x", metrics={}, blocked=True) == "guardrail"
    assert classify_failure(answer="  ", metrics={}) == "no_answer"
    assert classify_failure(answer="x", metrics={"faithfulness": 0.2}) == "hallucination"
    assert classify_failure(
        answer="x", metrics={"faithfulness": 0.9, "context_recall": 0.2}
    ) == "retrieval_missing"
    assert classify_failure(
        answer="x",
        metrics={"faithfulness": 0.9, "context_recall": 0.9, "context_precision": 0.2},
    ) == "retrieval_noise"
    assert classify_failure(
        answer="x",
        metrics={"faithfulness": 0.9, "context_recall": 0.9, "context_precision": 0.9,
                 "answer_relevancy": 0.2},
    ) == "off_topic"
    assert classify_failure(
        answer="x",
        metrics={"faithfulness": 0.9, "context_recall": 0.9, "context_precision": 0.9,
                 "answer_relevancy": 0.9},
        degraded=True,
    ) == "degraded"
    assert classify_failure(
        answer="x",
        metrics={"faithfulness": 0.9, "context_recall": 0.9, "context_precision": 0.9,
                 "answer_relevancy": 0.9},
    ) is None


def test_summarize_handles_missing_metrics() -> None:
    cases = [
        {"category": "fact", "metrics": {"faithfulness": 0.8, "answer_relevancy": 0.9},
         "failure_type": None, "chapter_hit_rate": 1.0, "latency_ms": 1000},
        {"category": "fact", "metrics": {"faithfulness": 0.4, "answer_relevancy": None},
         "failure_type": "hallucination", "chapter_hit_rate": 0.0, "latency_ms": 3000},
        {"category": "detail", "metrics": {"faithfulness": None, "answer_relevancy": 0.7},
         "failure_type": None, "chapter_hit_rate": None, "latency_ms": 2000},
    ]
    summary = summarize(cases)
    assert summary["case_count"] == 3
    assert summary["faithfulness"] == 0.6
    assert summary["faithfulness_count"] == 2
    assert summary["faithfulness_pass"] == 1
    assert summary["answer_relevancy_count"] == 2
    assert summary["scored_count"] == 3
    assert summary["failure_count"] == 1
    assert summary["pass_count"] == 2
    assert summary["pass_rate"] == 0.6667
    assert summary["chapter_hit_rate"] == 0.5
    assert summary["avg_latency_ms"] == 2000
    assert summary["failure_types"] == {"hallucination": 1}
    assert set(summary["by_category"]) == {"fact", "detail"}
    assert summary["by_category"]["fact"]["case_count"] == 2


def test_metric_delta_only_compares_available_values() -> None:
    current = {"faithfulness": 0.76, "answer_relevancy": 0.75, "context_precision": None}
    baseline = {"faithfulness": 0.63, "answer_relevancy": None, "context_precision": 0.79}
    assert metric_delta(current, baseline) == {"faithfulness": 0.13}
