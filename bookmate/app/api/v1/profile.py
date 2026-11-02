"""画像与推荐接口：问卷、画像读写、推荐、反馈。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser
from app.core.errors import ok
from app.schemas.profile import ProfileIn, QuestionnaireSubmit
from app.services import recommend as recommend_service
from app.services.questionnaire import build_questionnaire, profile_text, score_dims, top_traits
from app.utils.limiter import enforce

router = APIRouter(tags=["画像推荐"])


class FeedbackIn(BaseModel):
    book_id: int
    action: str = Field(pattern="^(like|dislike|save)$")
    comment: str | None = Field(default=None, max_length=500)


@router.get("/questionnaire")
async def get_questionnaire():
    return ok(build_questionnaire().model_dump())


@router.post("/questionnaire", status_code=status.HTTP_201_CREATED)
async def submit_questionnaire(body: QuestionnaireSubmit, user: CurrentUser, db: DB, request: Request):
    await enforce(request, "questionnaire", limit=10)
    dims, coverage = score_dims([answer.model_dump() for answer in body.answers])
    payload = ProfileIn(
        dims=dims,
        goal=body.goal,
        interests=body.interests,
        level=body.level,
        daily_minutes=body.daily_minutes,
        preferred_formats=body.preferred_formats,
        avoid_topics=body.avoid_topics,
    )
    profile = await recommend_service.save_profile(
        db,
        user,
        payload,
        dims=dims,
        confidence=coverage,
        source="questionnaire",
        reason=f"完成 {len(body.answers)}/{12} 题问卷",
    )
    return ok(
        {
            "profile": {**await recommend_service.profile_out(profile), "traits": top_traits(dims),
                        "summary": profile_text(dims, profile.goal, profile.interests or [],
                                                profile.level, profile.daily_minutes)},
            "coverage": coverage,
            "note": "画像已保存，可以生成推荐了。" if coverage >= 60 else "答题较少，推荐会偏通用，可补充问卷。",
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/profile")
async def get_profile(user: CurrentUser, db: DB):
    profile = await recommend_service.get_profile(db, user.id)
    if profile is None:
        return ok({"user_id": user.id, "completed": False, "profile": None})
    dims = profile.dims or {}
    return ok({"user_id": user.id, "completed": bool(profile.completed_at),
               "profile": {**await recommend_service.profile_out(profile), "traits": top_traits(dims),
                           "summary": profile_text(dims, profile.goal, profile.interests or [],
                                                   profile.level, profile.daily_minutes)}})


@router.put("/profile")
async def update_profile(body: ProfileIn, user: CurrentUser, db: DB):
    profile = await recommend_service.save_profile(db, user, body, source="manual")
    return ok(await recommend_service.profile_out(profile))


@router.get("/profile/history")
async def history(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=50)] = 10):
    return ok(await recommend_service.profile_history(db, user.id, limit))


@router.delete("/profile")
async def delete_profile(user: CurrentUser, db: DB):
    await recommend_service.delete_profile(db, user.id)
    return ok({"deleted": True})


@router.post("/recommendations")
async def create_recommendations(
    user: CurrentUser,
    db: DB,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
    refresh: Annotated[bool, Query()] = True,
):
    await enforce(request, "recommend", limit=12)
    if not refresh:
        cached = await recommend_service.latest_recommendations(db, user.id, limit)
        if cached["items"]:
            return ok(cached)
    return ok(await recommend_service.recommend_for_user(db, user, limit=limit))


@router.get("/recommendations/latest")
async def latest(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=10)] = 5):
    return ok(await recommend_service.latest_recommendations(db, user.id, limit))


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def feedback(body: FeedbackIn, user: CurrentUser, db: DB):
    row = await recommend_service.submit_feedback(db, user.id, body.book_id, body.action, body.comment)
    return ok({"id": row.id, "book_id": row.book_id, "action": row.action},
              status_code=status.HTTP_201_CREATED)
