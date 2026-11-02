"""推荐引擎：规则打分召回 → 在线 LLM 重排与理由 → 反馈闭环。

规则分四部分（总分 100）：
- 目标匹配 40：书目标签/分类/简介与阅读目标的关键词重合
- 水平匹配 20：难度与阅读水平中心值的距离
- 兴趣匹配 20：用户兴趣与书目标签重合数
- 性格/状态适配 10：五维倾向映射到题材与情绪
- 质量与可完成度 10：评分 + 预计完成天数
LLM 只允许在候选内重排（±12 分）并写理由；失败回退规则排序并标记 degraded。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.models.book import Book
from app.models.recommend import Feedback, Recommendation
from app.models.user import Profile, ProfileVersion, User
from app.schemas.profile import GOAL_LABELS, ProfileIn
from app.services.llm.client import get_llm_client
from app.services.llm.prompts import RECOMMEND_SYSTEM, recommend_user_prompt
from app.services.questionnaire import profile_text, top_traits, valid_level
from app.utils.timeutil import now_utc

logger = get_logger(__name__)

GOAL_KEYWORDS: dict[str, set[str]] = {
    "growth": {"成长", "自我", "人生", "哲学", "思考", "习惯", "勇气", "励志", "心理", "认知"},
    "career": {"职业", "工作", "管理", "效率", "决策", "沟通", "商业", "经济", "领导", "原则"},
    "emotion": {"治愈", "温柔", "情绪", "心理", "安慰", "生命", "孤独", "焦虑", "自我关怀"},
    "relationship": {"关系", "家庭", "爱情", "沟通", "亲密", "社交", "共情", "理解", "婚姻"},
    "habit": {"习惯", "行动", "效率", "自律", "方法", "实践", "改变", "坚持", "时间"},
    "knowledge": {"历史", "科普", "知识", "文明", "科学", "哲学", "社会", "人类", "世界"},
}
DIM_TAG_PREFERENCES: dict[str, set[str]] = {
    "openness": {"哲学", "历史", "科普", "思辨", "艺术", "文化", "人类学", "经典"},
    "conscientiousness": {"方法", "效率", "管理", "原则", "自律", "实践", "成长"},
    "extraversion": {"社会", "关系", "沟通", "传记", "商业", "领导"},
    "agreeableness": {"关系", "共情", "温柔", "治愈", "沟通", "家庭"},
    "neuroticism": {"治愈", "温柔", "心理", "安慰", "哲思", "平静", "自我关怀"},
}
LEVEL_CENTER = {"beginner": 1.5, "intermediate": 2.8, "advanced": 4.0}
AVOID_WEIGHT = 35
MAX_CANDIDATES = 10


def _book_text(book: Book) -> str:
    return " ".join([book.title, book.author, book.category, " ".join(book.tags or []), book.intro or ""])


def _overlap(tokens: set[str], text: str) -> int:
    return sum(1 for token in tokens if token and token in text)


def rule_score(book: Book, profile: Profile | None) -> tuple[int, dict]:
    text = _book_text(book)
    detail: dict = {}

    goal = profile.goal if profile else "growth"
    goal_hits = _overlap(GOAL_KEYWORDS.get(goal, set()), text)
    goal_score = min(40, goal_hits * 10)
    detail["goal"] = {"key": goal, "hits": goal_hits, "score": goal_score}

    level = valid_level(profile.level if profile else "beginner")
    center = LEVEL_CENTER[level]
    distance = abs(float(book.difficulty or 3) - center)
    level_score = max(0, int(round(20 - distance * 7)))
    detail["level"] = {"level": level, "difficulty": book.difficulty, "score": level_score}

    interests = [item for item in (profile.interests if profile else []) if item]
    interest_hits = sum(1 for item in interests if item in text)
    interest_score = min(20, interest_hits * 7)
    detail["interest"] = {"hits": interest_hits, "score": interest_score}

    dims = (profile.dims if profile and profile.dims else {}) or {}
    trait_hits = 0
    matched_traits: list[str] = []
    for dim, tags in DIM_TAG_PREFERENCES.items():
        value = int(dims.get(dim, 50))
        if value >= 65:
            hit = _overlap(tags, text)
            trait_hits += hit
            if hit:
                matched_traits.append(dim)
        elif value <= 35:
            # 低外向/低开放：偏短篇与低社交压力题材
            if dim == "extraversion" and book.difficulty <= 2:
                trait_hits += 1
            if dim == "openness" and book.total_minutes and book.total_minutes <= 240:
                trait_hits += 1
    trait_score = min(10, trait_hits * 3)
    detail["trait"] = {"traits": top_traits(dims) if dims else [], "matched": matched_traits, "score": trait_score}

    quality = min(6, int(round((book.rating or 80) / 100 * 6)))
    daily = max(int(profile.daily_minutes if profile else 30), 10)
    days = (book.total_minutes or 120) / daily
    finish_score = 4 if days <= 21 else (2 if days <= 45 else 0)
    detail["quality"] = {"rating": book.rating, "days": round(days, 1), "score": quality + finish_score}

    penalty = 0
    avoid = profile.avoid_topics if profile else []
    for topic in avoid:
        if topic and topic in text:
            penalty += AVOID_WEIGHT
    detail["avoid_penalty"] = penalty

    total = goal_score + level_score + interest_score + trait_score + quality + finish_score - penalty
    return max(0, min(100, total)), detail


def _feedback_bonus(actions: dict[int, str], book_id: int) -> int:
    action = actions.get(book_id)
    if action == "like":
        return 15
    if action == "save":
        return 10
    if action == "dislike":
        return -60
    return 0


async def _feedback_map(db: AsyncSession, user_id: int) -> dict[int, str]:
    rows = (
        await db.execute(select(Feedback.book_id, Feedback.action).where(Feedback.user_id == user_id))
    ).all()
    return {int(book_id): action for book_id, action in rows}


async def _candidates(db: AsyncSession, profile: Profile | None, feedback: dict[int, str]) -> list[tuple[Book, int, dict]]:
    books = list(
        (
            await db.execute(
                select(Book).where(Book.status == "published").order_by(Book.rating.desc()).limit(200)
            )
        ).scalars().all()
    )
    scored = []
    for book in books:
        score, detail = rule_score(book, profile)
        score = max(0, min(100, score + _feedback_bonus(feedback, book.id)))
        detail["feedback"] = feedback.get(book.id)
        detail["rule_score"] = score
        scored.append((book, score, detail))
    scored.sort(key=lambda item: (item[1], item[0].rating or 0), reverse=True)
    # 已点「不感兴趣」的不进入候选
    return [item for item in scored if feedback.get(item[0].id) != "dislike"][:MAX_CANDIDATES]


def _book_out(book: Book) -> dict:
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "category": book.category,
        "tags": book.tags or [],
        "intro": (book.intro or "")[:180],
        "difficulty": book.difficulty,
        "total_minutes": book.total_minutes,
        "cover_url": book.cover_url,
        "rating": book.rating,
        "chapter_count": book.chapter_count,
    }


async def _llm_rerank(
    profile: Profile | None,
    candidates: list[tuple[Book, int, dict]],
) -> tuple[list[dict], str, bool]:
    """返回 ([{book_id, score, reason, factor}], summary, degraded)。"""
    profile_summary = (
        profile_text(
            profile.dims or {},
            profile.goal,
            profile.interests or [],
            profile.level,
            profile.daily_minutes,
        )
        if profile
        else "未完成画像：按通用成长型读者处理"
    )
    goal_text = GOAL_LABELS.get(profile.goal if profile else "growth", "自我成长")
    payload = [
        {
            "id": book.id,
            "title": book.title,
            "author": book.author,
            "category": book.category,
            "tags": book.tags or [],
            "difficulty": book.difficulty,
            "total_minutes": book.total_minutes,
            "intro": book.intro or "",
        }
        for book, _score, _detail in candidates
    ]
    fallback = [
        {
            "book_id": book.id,
            "score": score,
            "reason": _rule_reason(book, score, detail),
            "factor": detail,
        }
        for book, score, detail in candidates[:5]
    ]
    try:
        data = await get_llm_client().chat_json(
            [
                {"role": "system", "content": RECOMMEND_SYSTEM},
                {"role": "user", "content": recommend_user_prompt(profile_summary, goal_text, payload)},
            ],
            temperature=0.4,
            max_tokens=1200,
            operation="recommend",
        )
    except Exception as exc:  # noqa: BLE001 - 推荐允许降级
        logger.warning("recommend_llm_degraded", extra={"extra_fields": {"error": type(exc).__name__}})
        return fallback, "模型暂不可用，先按画像规则为你挑选。", True

    allowed = {book.id: (book, score, detail) for book, score, detail in candidates}
    picks = []
    seen: set[int] = set()
    for item in data.get("picks") or []:
        try:
            book_id = int(item.get("book_id"))
        except (TypeError, ValueError):
            continue
        if book_id not in allowed or book_id in seen:
            continue
        book, base_score, detail = allowed[book_id]
        try:
            llm_score = int(item.get("score", base_score))
        except (TypeError, ValueError):
            llm_score = base_score
        score = int(round(base_score * 0.72 + max(0, min(100, llm_score)) * 0.28))
        seesaw = score - base_score
        score = max(0, min(100, base_score + max(-12, min(12, seesaw))))
        reason = str(item.get("reason") or "").strip()[:200] or _rule_reason(book, score, detail)
        picks.append({"book_id": book_id, "score": score, "reason": reason, "factor": detail})
        seen.add(book_id)
        if len(picks) >= 5:
            break
    if not picks:
        return fallback, "模型返回为空，先按画像规则为你挑选。", True
    summary = str(data.get("summary") or "结合你的目标与性格，优先选了难度匹配、能读得下去的书。")[:160]
    return picks, summary, False


def _rule_reason(book: Book, score: int, detail: dict) -> str:
    reasons = []
    if detail.get("goal", {}).get("hits"):
        reasons.append(f"贴合你的{GOAL_LABELS.get(detail['goal']['key'], '阅读目标')}")
    if detail.get("interest", {}).get("hits"):
        reasons.append("命中你感兴趣的主题")
    if detail.get("level", {}).get("score", 0) >= 14:
        reasons.append(f"难度 {book.difficulty}/5 与你的水平匹配")
    if detail.get("trait", {}).get("matched"):
        reasons.append("气质与你的性格倾向相合")
    if detail.get("quality", {}).get("days", 99) <= 14:
        reasons.append("两周内可以读完")
    if not reasons:
        reasons.append("口碑稳定，适合作为拓展阅读")
    return "；".join(reasons[:3]) + "。"


async def save_profile(
    db: AsyncSession,
    user: User,
    payload: ProfileIn,
    *,
    dims: dict[str, int] | None = None,
    confidence: int | None = None,
    source: str = "questionnaire",
    reason: str | None = None,
) -> Profile:
    profile = await db.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
    profile.dims = dims or payload.dims
    profile.goal = payload.goal
    profile.interests = payload.interests
    profile.level = payload.level
    profile.daily_minutes = payload.daily_minutes
    profile.preferred_formats = payload.preferred_formats
    profile.avoid_topics = payload.avoid_topics
    profile.confidence = confidence if confidence is not None else max(profile.confidence, 60)
    profile.version = int(profile.version or 1) + 1
    profile.completed_at = now_utc()
    await db.flush()
    db.add(
        ProfileVersion(
            user_id=user.id,
            version=profile.version,
            dims=profile.dims,
            goal=profile.goal,
            interests=profile.interests,
            source=source,
            reason=reason,
        )
    )
    await db.commit()
    await db.refresh(profile)
    logger.info(
        "profile_saved",
        extra={"extra_fields": {"user_id": user.id, "version": profile.version, "source": source}},
    )
    return profile


async def get_profile(db: AsyncSession, user_id: int) -> Profile | None:
    return await db.get(Profile, user_id)


async def profile_out(profile: Profile) -> dict:
    from app.schemas.profile import DIM_LABELS, GOAL_LABELS

    return {
        "user_id": profile.user_id,
        "dims": profile.dims or {},
        "goal": profile.goal,
        "interests": profile.interests or [],
        "level": profile.level,
        "daily_minutes": profile.daily_minutes,
        "preferred_formats": profile.preferred_formats or [],
        "avoid_topics": profile.avoid_topics or [],
        "confidence": profile.confidence,
        "version": profile.version,
        "completed_at": profile.completed_at,
        "updated_at": profile.updated_at,
        "labels": dict(DIM_LABELS),
        "goal_label": GOAL_LABELS.get(profile.goal, profile.goal),
        "level_label": {"beginner": "入门", "intermediate": "进阶", "advanced": "高阶"}.get(profile.level, profile.level),
    }


async def recommend_for_user(
    db: AsyncSession,
    user: User,
    *,
    limit: int = 5,
    refresh: bool = False,
) -> dict:
    limit = max(1, min(limit, 10))
    profile = await get_profile(db, user.id)
    feedback = await _feedback_map(db, user.id)
    candidates = await _candidates(db, profile, feedback)
    if not candidates:
        raise AppError(code="NO_BOOKS", status_code=503, message="书目为空，请先导入书籍")

    picks, summary, degraded = await _llm_rerank(profile, candidates)
    picks = picks[:limit]
    for pick in picks:
        pick.setdefault("factor", {})["batch_summary"] = summary
    batch_id = str(uuid.uuid4())
    for rank, pick in enumerate(picks, start=1):
        book = next((candidate[0] for candidate in candidates if candidate[0].id == pick["book_id"]), None)
        if book is None:
            continue
        db.add(
            Recommendation(
                user_id=user.id,
                book_id=book.id,
                batch_id=batch_id,
                rank=rank,
                score=int(pick["score"]),
                rule_score=int(pick.get("factor", {}).get("rule_score", pick["score"])),
                reason=pick["reason"],
                factors=pick.get("factor", {}),
                items=[_book_out(item[0]) for item in candidates],
                degraded=degraded,
                source="llm+rules" if not degraded else "rules",
            )
        )
    await db.commit()

    books = {book.id: book for book in (candidate[0] for candidate in candidates)}
    items = []
    for rank, pick in enumerate(picks, start=1):
        book = books.get(pick["book_id"])
        if book is None:
            continue
        items.append(
            {
                **_book_out(book),
                "rank": rank,
                "score": pick["score"],
                "rule_score": pick.get("factor", {}).get("rule_score"),
                "reason": pick["reason"],
                "factors": pick.get("factor", {}),
            }
        )
    return {
        "batch_id": batch_id,
        "summary": summary,
        "items": items,
        "degraded": degraded,
        "profile_completed": bool(profile and profile.completed_at),
        "profile_version": profile.version if profile else 0,
    }


async def latest_recommendations(db: AsyncSession, user_id: int, limit: int = 5) -> dict:
    batch_id = await db.scalar(
        select(Recommendation.batch_id)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.id.desc())
        .limit(1)
    )
    if not batch_id:
        return {"batch_id": None, "items": [], "summary": "还没有推荐记录"}
    rows = (
        await db.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id, Recommendation.batch_id == batch_id)
            .order_by(Recommendation.rank)
            .limit(limit)
        )
    ).scalars().all()
    book_ids = [row.book_id for row in rows]
    books = {
        book.id: book
        for book in (
            await db.execute(select(Book).where(Book.id.in_(book_ids)))
        ).scalars().all()
    } if book_ids else {}
    return {
        "batch_id": batch_id,
        "items": [
            {
                **(_book_out(books[row.book_id]) if row.book_id in books else {"id": row.book_id}),
                "rank": row.rank,
                "score": row.score,
                "rule_score": row.rule_score,
                "reason": row.reason,
                "factors": row.factors,
            }
            for row in rows
        ],
        "summary": (rows[0].factors or {}).get("batch_summary", "") if rows else "",
        "degraded": any(row.degraded for row in rows),
    }


async def submit_feedback(
    db: AsyncSession,
    user_id: int,
    book_id: int,
    action: str,
    comment: str | None = None,
    source: str = "recommend",
) -> Feedback:
    if action not in {"like", "dislike", "save"}:
        raise AppError(code="BAD_ACTION", status_code=400, message="action 需为 like/dislike/save")
    if await db.get(Book, book_id) is None:
        raise not_found("书籍不存在")
    existing = await db.scalar(
        select(Feedback).where(
            Feedback.user_id == user_id, Feedback.book_id == book_id, Feedback.action == action
        )
    )
    if existing:
        existing.comment = comment
        existing.source = source
        feedback = existing
    else:
        feedback = Feedback(
            user_id=user_id, book_id=book_id, action=action, comment=comment, source=source
        )
        db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback


async def profile_history(db: AsyncSession, user_id: int, limit: int = 10) -> list[dict]:
    rows = (
        await db.execute(
            select(ProfileVersion)
            .where(ProfileVersion.user_id == user_id, ProfileVersion.is_deleted.is_(False))
            .order_by(ProfileVersion.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "version": row.version,
            "dims": row.dims,
            "goal": row.goal,
            "interests": row.interests,
            "source": row.source,
            "reason": row.reason,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def delete_profile(db: AsyncSession, user_id: int) -> None:
    profile = await db.get(Profile, user_id)
    if profile is None:
        return
    await db.execute(
        ProfileVersion.__table__.update()
        .where(ProfileVersion.user_id == user_id)
        .values(is_deleted=True)
    )
    await db.delete(profile)
    await db.commit()
