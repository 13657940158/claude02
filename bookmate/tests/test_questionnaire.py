"""问卷计分与画像标签测试（纯函数）。"""
from app.schemas.profile import DIM_KEYS
from app.services.questionnaire import (
    QUESTIONS,
    build_questionnaire,
    dim_labels,
    score_dims,
    top_traits,
    valid_level,
)


def test_build_questionnaire_shape() -> None:
    data = build_questionnaire()
    assert data.version
    assert len(data.questions) == 12
    assert len(data.goals) >= 3
    assert {level["key"] for level in data.levels} == {"beginner", "intermediate", "advanced"}


def test_score_dims_full_coverage() -> None:
    answers = [{"question_id": q["id"], "option_key": "a"} for q in QUESTIONS]
    dims, coverage = score_dims(answers)
    assert coverage == 100
    assert set(dims) == set(DIM_KEYS)
    assert all(0 <= value <= 100 for value in dims.values())


def test_score_dims_partial_and_unknown() -> None:
    answers = [{"question_id": q["id"], "option_key": "a"} for q in QUESTIONS[:6]]
    answers.append({"question_id": 999, "option_key": "a"})
    answers.append({"question_id": 1, "option_key": "不存在"})
    dims, coverage = score_dims(answers)
    assert coverage == 50
    assert set(dims) == set(DIM_KEYS)


def test_top_traits_falls_back_to_highest_dim() -> None:
    dims = {key: 50 for key in DIM_KEYS}
    assert top_traits(dims)  # 全 50 时也会给出最高维
    dims["openness"] = 90
    assert top_traits(dims)[0] == "开放探索"
    assert len(top_traits(dims, limit=2)) <= 2


def test_dim_labels_and_level_validation() -> None:
    labels = dim_labels({key: 50 for key in DIM_KEYS})
    assert len(labels) == len(DIM_KEYS)
    assert all(isinstance(value, str) and value for value in labels.values())
    assert valid_level("advanced") == "advanced"
    assert valid_level("whatever") == "beginner"
