"""画像 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

DIM_KEYS = ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")
DIM_LABELS = {
    "openness": "开放探索",
    "conscientiousness": "自律条理",
    "extraversion": "外向社交",
    "agreeableness": "共情随和",
    "neuroticism": "情绪敏感",
}
GOALS = ("growth", "career", "emotion", "relationship", "habit", "knowledge")
GOAL_LABELS = {
    "growth": "自我成长",
    "career": "职业进阶",
    "emotion": "情绪疗愈",
    "relationship": "关系理解",
    "habit": "习惯养成",
    "knowledge": "知识拓展",
}
LEVELS = ("beginner", "intermediate", "advanced")


class ProfileIn(BaseModel):
    dims: dict[str, int]
    goal: str = "growth"
    interests: list[str] = Field(default_factory=list, max_length=10)
    level: str = "beginner"
    daily_minutes: int = Field(default=30, ge=10, le=240)
    preferred_formats: list[str] = Field(default_factory=list, max_length=6)
    avoid_topics: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("dims")
    @classmethod
    def check_dims(cls, value: dict[str, int]) -> dict[str, int]:
        missing = [key for key in DIM_KEYS if key not in value]
        if missing:
            raise ValueError(f"缺少维度：{','.join(missing)}")
        cleaned: dict[str, int] = {}
        for key in DIM_KEYS:
            try:
                score = int(value[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"维度 {key} 必须是 0-100 的整数") from exc
            if not 0 <= score <= 100:
                raise ValueError(f"维度 {key} 超出 0-100")
            cleaned[key] = score
        return cleaned

    @field_validator("goal")
    @classmethod
    def check_goal(cls, value: str) -> str:
        if value not in GOALS:
            raise ValueError(f"目标需为：{','.join(GOALS)}")
        return value

    @field_validator("level")
    @classmethod
    def check_level(cls, value: str) -> str:
        if value not in LEVELS:
            raise ValueError(f"阅读水平需为：{','.join(LEVELS)}")
        return value

    @field_validator("interests", "avoid_topics", "preferred_formats")
    @classmethod
    def clean_list(cls, value: list[str]) -> list[str]:
        return [item.strip()[:32] for item in value if item.strip()]


class ProfileOut(BaseModel):
    user_id: int
    dims: dict[str, int]
    goal: str
    interests: list[str]
    level: str
    daily_minutes: int
    preferred_formats: list[str] = Field(default_factory=list)
    avoid_topics: list[str] = Field(default_factory=list)
    confidence: int
    version: int
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    goal_label: str = ""
    level_label: str = ""


class ProfileVersionOut(BaseModel):
    id: int
    version: int
    dims: dict[str, int]
    goal: str
    interests: list[str]
    source: str
    reason: str | None
    created_at: datetime


class QuestionnaireOption(BaseModel):
    key: str
    text: str
    scores: dict[str, int]


class QuestionnaireQuestion(BaseModel):
    id: int
    text: str
    hint: str = ""
    dims: list[str]
    options: list[QuestionnaireOption]


class QuestionnaireOut(BaseModel):
    version: str
    questions: list[QuestionnaireQuestion]
    goals: list[dict[str, str]]
    levels: list[dict[str, str]]
    note: str


class QuestionnaireAnswer(BaseModel):
    question_id: int
    option_key: str


class QuestionnaireSubmit(BaseModel):
    answers: list[QuestionnaireAnswer]
    goal: str
    interests: list[str] = Field(default_factory=list, max_length=10)
    level: str = "beginner"
    daily_minutes: int = Field(default=30, ge=10, le=240)
    preferred_formats: list[str] = Field(default_factory=list, max_length=6)
    avoid_topics: list[str] = Field(default_factory=list, max_length=10)
    consent: bool = True

    @field_validator("goal")
    @classmethod
    def check_goal(cls, value: str) -> str:
        if value not in GOALS:
            raise ValueError(f"目标需为：{','.join(GOALS)}")
        return value

    @field_validator("level")
    @classmethod
    def check_level(cls, value: str) -> str:
        if value not in LEVELS:
            raise ValueError(f"阅读水平需为：{','.join(LEVELS)}")
        return value
