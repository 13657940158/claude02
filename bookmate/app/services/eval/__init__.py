"""RAGAS 评测子模块：数据集、指标、报告与调优建议。"""

from app.services.eval.dataset import EvalCaseSpec, load_dataset
from app.services.eval.report import METRIC_KEYS, THRESHOLDS, summarize

__all__ = ["EvalCaseSpec", "load_dataset", "METRIC_KEYS", "THRESHOLDS", "summarize"]
