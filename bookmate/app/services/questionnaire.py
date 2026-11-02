"""阅读人格问卷：12 题五维测评 + 目标/兴趣/水平采集。

计分：每题选项对若干维度加减分，按维度题数归一化到 0-100；
confidence 由答题覆盖率决定，低于 60% 时推荐降级为「轻画像」模式。
"""
from __future__ import annotations

from app.schemas.profile import DIM_KEYS, DIM_LABELS, GOAL_LABELS, GOALS, LEVELS, QuestionnaireOut

QUESTIONNAIRE_VERSION = "q2026.1"
MAX_WEIGHT_SLOTS = 20  # 每个维度理论可答题位（用于归一化）

QUESTIONS: list[dict] = [
    {
        "id": 1,
        "text": "下班后有一段空闲的晚上，你更想：",
        "hint": "关于你对新体验的渴望程度",
        "dims": ["openness", "extraversion"],
        "options": [
            {"key": "a", "text": "去一家没去过的小店或看场展览", "scores": {"openness": 10, "extraversion": 6}},
            {"key": "b", "text": "和熟悉的朋友吃顿饭聊聊天", "scores": {"openness": 3, "extraversion": 8}},
            {"key": "c", "text": "在家读点书或追剧", "scores": {"openness": 4, "extraversion": -6}},
            {"key": "d", "text": "整理房间、把待办清一清", "scores": {"openness": -2, "conscientiousness": 10}},
        ],
    },
    {
        "id": 2,
        "text": "面对一个复杂的新问题，你的第一反应是：",
        "dims": ["openness", "conscientiousness"],
        "options": [
            {"key": "a", "text": "兴奋，想先多了解各种可能性", "scores": {"openness": 10, "conscientiousness": -2}},
            {"key": "b", "text": "先列一个清单，把步骤拆开", "scores": {"conscientiousness": 10, "openness": 2}},
            {"key": "c", "text": "先问问身边的人怎么想", "scores": {"agreeableness": 8, "openness": 3}},
            {"key": "d", "text": "有点烦，想等一等再说", "scores": {"neuroticism": 6, "conscientiousness": -6}},
        ],
    },
    {
        "id": 3,
        "text": "旅行前你通常：",
        "dims": ["openness", "conscientiousness"],
        "options": [
            {"key": "a", "text": "订好机票就去，到了再看", "scores": {"openness": 9, "conscientiousness": -8}},
            {"key": "b", "text": "做详细攻略，连餐厅都排好", "scores": {"conscientiousness": 10, "openness": -2}},
            {"key": "c", "text": "列几个必去的地方就够了", "scores": {"openness": 5, "conscientiousness": 4}},
            {"key": "d", "text": "尽量待在家里休息", "scores": {"extraversion": -8, "openness": -4}},
        ],
    },
    {
        "id": 4,
        "text": "你的书桌/工位一般是：",
        "dims": ["conscientiousness"],
        "options": [
            {"key": "a", "text": "干净整齐，东西各有位置", "scores": {"conscientiousness": 10}},
            {"key": "b", "text": "有点乱但我知道东西在哪", "scores": {"conscientiousness": 2}},
            {"key": "c", "text": "经常堆满，想起来再收拾", "scores": {"conscientiousness": -8}},
            {"key": "d", "text": "看心情，忙起来就顾不上", "scores": {"conscientiousness": -3, "neuroticism": 3}},
        ],
    },
    {
        "id": 5,
        "text": "聚会结束后，你通常：",
        "dims": ["extraversion"],
        "options": [
            {"key": "a", "text": "很兴奋，还想再聊一会儿", "scores": {"extraversion": 10}},
            {"key": "b", "text": "挺开心，但想早点回家", "scores": {"extraversion": 2}},
            {"key": "c", "text": "觉得累了，需要独处充电", "scores": {"extraversion": -9}},
            {"key": "d", "text": "全程有点放不开", "scores": {"extraversion": -6, "neuroticism": 5}},
        ],
    },
    {
        "id": 6,
        "text": "需要当众发言时，你：",
        "dims": ["extraversion", "neuroticism"],
        "options": [
            {"key": "a", "text": "挺享受，能带动气氛", "scores": {"extraversion": 10, "neuroticism": -5}},
            {"key": "b", "text": "准备好就不紧张", "scores": {"extraversion": 4, "conscientiousness": 5}},
            {"key": "c", "text": "会紧张，但能撑过去", "scores": {"extraversion": -3, "neuroticism": 5}},
            {"key": "d", "text": "能躲就躲，手心冒汗", "scores": {"extraversion": -8, "neuroticism": 10}},
        ],
    },
    {
        "id": 7,
        "text": "朋友向你倾诉烦恼时，你更常：",
        "dims": ["agreeableness"],
        "options": [
            {"key": "a", "text": "先耐心听，陪 TA 把情绪说出来", "scores": {"agreeableness": 10}},
            {"key": "b", "text": "帮 TA 分析问题、给建议", "scores": {"agreeableness": 4, "conscientiousness": 5}},
            {"key": "c", "text": "会跟着一起着急上火", "scores": {"agreeableness": 6, "neuroticism": 8}},
            {"key": "d", "text": "不太擅长，尽量岔开话题", "scores": {"agreeableness": -8}},
        ],
    },
    {
        "id": 8,
        "text": "和同事/同学有分歧时，你通常：",
        "dims": ["agreeableness", "neuroticism"],
        "options": [
            {"key": "a", "text": "先让步，让关系保持融洽", "scores": {"agreeableness": 10, "neuroticism": 3}},
            {"key": "b", "text": "摆事实讲道理，争到说清为止", "scores": {"agreeableness": -6, "conscientiousness": 6}},
            {"key": "c", "text": "各退一步，找折中方案", "scores": {"agreeableness": 8, "conscientiousness": 4}},
            {"key": "d", "text": "表面没事，心里别扭很久", "scores": {"agreeableness": 2, "neuroticism": 10}},
        ],
    },
    {
        "id": 9,
        "text": "遇到计划之外的变动，你：",
        "dims": ["neuroticism"],
        "options": [
            {"key": "a", "text": "很快适应，看情况调整", "scores": {"neuroticism": -10, "openness": 5}},
            {"key": "b", "text": "会有点烦，但能接受", "scores": {"neuroticism": 3}},
            {"key": "c", "text": "需要一段时间消化", "scores": {"neuroticism": 7}},
            {"key": "d", "text": "会明显焦虑，反复想哪里出了问题", "scores": {"neuroticism": 10}},
        ],
    },
    {
        "id": 10,
        "text": "最近一段时间，你的情绪：",
        "dims": ["neuroticism"],
        "options": [
            {"key": "a", "text": "总体平稳，很少大起大落", "scores": {"neuroticism": -10}},
            {"key": "b", "text": "偶尔低落，能自己调整", "scores": {"neuroticism": 0}},
            {"key": "c", "text": "起伏比较多，容易受影响", "scores": {"neuroticism": 7}},
            {"key": "d", "text": "经常紧绷或担心", "scores": {"neuroticism": 10}},
        ],
    },
    {
        "id": 11,
        "text": "睡前你常在做什么？",
        "dims": ["neuroticism", "conscientiousness"],
        "options": [
            {"key": "a", "text": "读一会儿书就读睡了", "scores": {"neuroticism": -4, "conscientiousness": 6}},
            {"key": "b", "text": "刷手机刷到很晚", "scores": {"conscientiousness": -6, "neuroticism": 4}},
            {"key": "c", "text": "想事情，有时睡不着", "scores": {"neuroticism": 7, "conscientiousness": -2}},
            {"key": "d", "text": "有固定的放松仪式", "scores": {"neuroticism": -6, "conscientiousness": 5}},
        ],
    },
    {
        "id": 12,
        "text": "你认为自己更像：",
        "dims": ["openness", "agreeableness"],
        "options": [
            {"key": "a", "text": "爱琢磨抽象问题的人", "scores": {"openness": 9, "agreeableness": 2}},
            {"key": "b", "text": "靠谱、能把事做完的人", "scores": {"conscientiousness": 9}},
            {"key": "c", "text": "愿意照顾别人感受的人", "scores": {"agreeableness": 9}},
            {"key": "d", "text": "还在寻找自己方向的人", "scores": {"openness": 5, "neuroticism": 3}},
        ],
    },
]

_QUESTIONS_BY_ID = {item["id"]: item for item in QUESTIONS}


def build_questionnaire() -> QuestionnaireOut:
    return QuestionnaireOut(
        version=QUESTIONNAIRE_VERSION,
        questions=QUESTIONS,
        goals=[{"key": key, "label": GOAL_LABELS[key]} for key in GOALS],
        levels=[
            {"key": "beginner", "label": "刚开始读，想轻松进入"},
            {"key": "intermediate", "label": "每周能读一点，想稳定下来"},
            {"key": "advanced", "label": "读得不少，想读得更有深度"},
        ],
        note="共 12 题，约 3 分钟。答案只用于个性化推荐与导读，可在「我的画像」随时修改或删除。",
    )


def score_dims(answers: list[dict]) -> tuple[dict[str, int], int]:
    """answers: [{question_id, option_key}]；返回 (五维 0-100, 覆盖率 0-100)。"""
    raw = {key: 50.0 for key in DIM_KEYS}
    answered = 0
    for answer in answers:
        question = _QUESTIONS_BY_ID.get(int(answer.get("question_id", 0)))
        if question is None:
            continue
        option = next((o for o in question["options"] if o["key"] == answer.get("option_key")), None)
        if option is None:
            continue
        answered += 1
        for dim, delta in option["scores"].items():
            if dim in raw:
                # 每个选项权重上限 ±10；单题最多 ±10，避免单题决定维度
                raw[dim] += max(-10, min(10, int(delta)))
    dims = {key: int(max(0, min(100, round(value)))) for key, value in raw.items()}
    coverage = int(round(answered / len(QUESTIONS) * 100))
    return dims, coverage


def dim_labels(dims: dict[str, int]) -> dict[str, str]:
    return {
        key: f"{DIM_LABELS[key]}{'高' if dims.get(key, 50) >= 60 else ('低' if dims.get(key, 50) <= 40 else '中')}"
        for key in DIM_KEYS
    }


def top_traits(dims: dict[str, int], limit: int = 3) -> list[str]:
    if not dims:
        return []
    ranked = sorted(dims.items(), key=lambda item: item[1], reverse=True)
    traits = []
    for key, value in ranked[:limit]:
        if value >= 55:
            traits.append(DIM_LABELS.get(key, key))
    if not traits:
        traits = [DIM_LABELS.get(ranked[0][0], ranked[0][0])]
    return traits


def profile_text(dims: dict[str, int], goal: str, interests: list[str], level: str, daily_minutes: int) -> str:
    """给推荐模型看的画像摘要（自然语言，避免直接泄露过于敏感的情绪标签）。"""
    parts = [f"阅读目标：{GOAL_LABELS.get(goal, goal)}"]
    traits = top_traits(dims)
    if traits:
        parts.append(f"性格倾向：{'、'.join(traits)}")
    if dims.get("neuroticism", 50) >= 65:
        parts.append("情绪较敏感，适合温和、有支持感的读物，避免过于沉重或高压")
    if dims.get("conscientiousness", 50) <= 40:
        parts.append("执行计划容易中断，适合短篇、低门槛、能快速看到进展的书")
    if interests:
        parts.append(f"感兴趣：{'、'.join(interests[:6])}")
    parts.append(f"阅读水平：{ {'beginner': '入门', 'intermediate': '进阶', 'advanced': '高阶'}.get(level, level) }")
    parts.append(f"每天可投入：{daily_minutes} 分钟")
    return "；".join(parts)


def valid_level(level: str) -> str:
    return level if level in LEVELS else "beginner"
