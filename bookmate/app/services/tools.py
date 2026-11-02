"""客服工具：书目检索、书籍解析、推荐、进度、RAG 内容、四域查询。

不依赖模型原生的 function-calling：用可解释的意图规则挑工具，保证在线模型兼容性；
每次调用统一落 tool_calls 审计，失败只降级该工具，不影响整体回答。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.book import Book
from app.models.chat import ToolCall
from app.models.reading import Progress, ReadingPlan
from app.models.user import User
from app.services import books as book_service
from app.services import domains as domain_service
from app.services import recommend as recommend_service
from app.services.rag.analysis import analyze_book, analysis_out
from app.services.rag.retrieval import retrieve

logger = get_logger(__name__)

TOOL_LABELS = {
    "search_books": "检索书目",
    "book_analysis": "书籍解析",
    "recommend_books": "智能荐书",
    "reading_progress": "阅读进度",
    "retrieve_content": "原文检索",
    "audio_data": "听书资源",
    "edu_data": "学习资源",
    "travel_data": "文旅资源",
    "finance_data": "购书金融",
}

INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("retrieve_content", re.compile(r"(书中|原文|这段|这句话|解释|什么意思|含义|观点|讲了什么|主要内容|为什么|如何理解)")),
    ("recommend_books", re.compile(r"(推荐|选书|读什么|适合我|书单|想读|找一本|想找|有什么书|适合.{0,8}读)")),
    ("reading_progress", re.compile(r"(进度|读到哪|计划|坚持|打卡|读了多少)")),
    ("audio_data", re.compile(r"(听书|有声|音频|播客|通勤听)")),
    ("edu_data", re.compile(r"(课程|学习资源|练习|精读课|教育)")),
    ("travel_data", re.compile(r"(旅行|文旅|城市|书店|图书馆|边走边读)")),
    ("finance_data", re.compile(r"(预算|价格|多少钱|购书|理财|财商)")),
    ("book_analysis", re.compile(r"(解析|拆书|这本书讲|评价这本书|主题|金句)")),
]


@dataclass
class ToolInvocation:
    name: str
    args: dict = field(default_factory=dict)
    status: str = "ok"
    latency_ms: int = 0
    result: dict = field(default_factory=dict)
    error: str | None = None
    citations: list[dict] = field(default_factory=list)

    @property
    def result_size(self) -> int:
        try:
            return len(str(self.result))
        except Exception:  # noqa: BLE001
            return 0

    def summary_for_prompt(self) -> str:
        if self.status != "ok":
            return f"[{self.name}] 暂不可用：{self.error or '未知错误'}"
        return f"[{self.name}] {self.result.get('summary') or ''}"

    def out(self) -> dict:
        return {
            "name": self.name,
            "label": TOOL_LABELS.get(self.name, self.name),
            "status": self.status,
            "latency_ms": self.latency_ms,
            "args": self.args,
            "summary": self.result.get("summary") if isinstance(self.result, dict) else "",
        }


async def _resolve_book(db: AsyncSession, query: str, book_id: int | None) -> Book | None:
    if book_id:
        return await db.get(Book, book_id)
    match = re.search(r"《([^》]{1,40})》", query)
    if match:
        title = match.group(1).strip()
        return await db.scalar(select(Book).where(Book.title.like(f"%{title}%"), Book.status == "published"))
    return None


def plan_tools(query: str, *, has_book: bool = False, include_domains: bool = True) -> list[str]:
    names: list[str] = []
    for name, pattern in INTENT_PATTERNS:
        if not include_domains and name.endswith("_data"):
            continue
        if pattern.search(query):
            names.append(name)
    if has_book and "book_analysis" not in names:
        names.append("book_analysis")
    # 去重、限流：最多 3 个工具，优先内容与推荐
    priority = {"retrieve_content": 0, "recommend_books": 1, "book_analysis": 2,
                "reading_progress": 3, "audio_data": 4, "edu_data": 5, "travel_data": 6,
                "finance_data": 7, "search_books": 8}
    names = sorted(dict.fromkeys(names), key=lambda n: priority.get(n, 9))[:3]
    return names


async def _run_search_books(db: AsyncSession, query: str, book_id: int | None) -> ToolInvocation:
    book = await _resolve_book(db, query, book_id)
    if book:
        return ToolInvocation(
            name="search_books",
            result={"summary": f"找到《{book.title}》（{book.author}），难度 {book.difficulty}/5。",
                    "books": [{"id": book.id, "title": book.title, "author": book.author,
                               "category": book.category, "intro": book.intro[:120]}]},
        )
    items, total = await book_service.list_books(db, page=1, page_size=5, keyword=query[:40])
    return ToolInvocation(
        name="search_books",
        result={
            "summary": f"按关键词找到 {total} 本书。" if total else "书目里没有直接匹配的书，可以换个关键词。",
            "books": [{"id": b.id, "title": b.title, "author": b.author, "category": b.category}
                      for b in items],
        },
    )


async def _run_book_analysis(db: AsyncSession, query: str, book_id: int | None) -> ToolInvocation:
    book = await _resolve_book(db, query, book_id)
    if book is None:
        return ToolInvocation(name="book_analysis", status="error", error="未指定可识别的书名")
    record = await analyze_book(db, book.id, chapter_limit=8)
    data = analysis_out(record, book)
    return ToolInvocation(
        name="book_analysis",
        result={
            "summary": data.get("one_liner") or f"《{book.title}》的结构化解析已生成。",
            "themes": data.get("themes", []),
            "key_points": data.get("key_points", [])[:4],
            "quotes": data.get("quotes", [])[:3],
            "reading_tips": data.get("reading_tips", [])[:3],
            "status": data.get("status"),
        },
    )


async def _run_recommend(db: AsyncSession, user: User | None, query: str) -> ToolInvocation:
    if user is None:
        return ToolInvocation(name="recommend_books", status="denied", error="登录后可生成个性化推荐")
    data = await recommend_service.recommend_for_user(db, user, limit=4)
    return ToolInvocation(
        name="recommend_books",
        result={
            "summary": data.get("summary") or "已按你的画像挑选 4 本书。",
            "items": [
                {"id": item["id"], "title": item["title"], "author": item["author"],
                 "score": item["score"], "reason": item["reason"]}
                for item in data.get("items", [])
            ],
            "degraded": data.get("degraded"),
        },
    )


async def _run_progress(db: AsyncSession, user: User | None) -> ToolInvocation:
    if user is None:
        return ToolInvocation(name="reading_progress", status="denied", error="登录后可查看阅读进度")
    plans = (
        await db.execute(
            select(ReadingPlan).where(ReadingPlan.user_id == user.id)
            .order_by(ReadingPlan.id.desc()).limit(3)
        )
    ).scalars().all()
    progress_rows = (
        await db.execute(select(Progress).where(Progress.user_id == user.id).order_by(Progress.id.desc()).limit(5))
    ).scalars().all()
    return ToolInvocation(
        name="reading_progress",
        result={
            "summary": f"你有 {len(plans)} 个近期计划和 {len(progress_rows)} 条阅读进度。",
            "plans": [{"id": p.id, "book_id": p.book_id, "days": p.days, "current_day": p.current_day,
                       "status": p.status, "completed_sessions": p.completed_sessions} for p in plans],
            "progress": [{"book_id": p.book_id, "chapter_no": p.chapter_no, "percent": p.percent,
                          "streak_days": p.streak_days, "total_minutes": p.total_minutes} for p in progress_rows],
        },
    )


async def _run_retrieve(db: AsyncSession, query: str, book_id: int | None) -> ToolInvocation:
    book = await _resolve_book(db, query, book_id)
    result = await retrieve(db, query, top_k=12, rerank_top_n=5, book_ids=[book.id] if book else None)
    if not result.chunks:
        return ToolInvocation(
            name="retrieve_content",
            result={"summary": "原文里没有检索到直接相关的内容。", "contexts": []},
        )
    return ToolInvocation(
        name="retrieve_content",
        result={
            "summary": f"从 {len({c.book_id for c in result.chunks})} 本书中检索到 {len(result.chunks)} 段相关原文。",
            "contexts": [chunk.context_text() for chunk in result.chunks],
            "chunks": [chunk.as_citation() for chunk in result.chunks],
            "degraded": result.degraded,
        },
        citations=result.citations(),
    )


_DOMAIN_NOISE = re.compile(r"(有没有|有哪些|我想|想要|可以|帮我|查一下|找一下|推荐|听听|看看|吗|呢|？|\?)")


async def _run_domain(db: AsyncSession, name: str, query: str) -> ToolInvocation:
    domain = name.replace("_data", "")
    book = await _resolve_book(db, query, None)
    topic = book.title if book else _DOMAIN_NOISE.sub(" ", query).strip()[:30]
    topic = topic or "这本书"
    result = await domain_service.call_domain(domain, topic, db=db)
    return ToolInvocation(
        name=name,
        status="ok" if result.ok else "error",
        result={"summary": result.summary, "items": result.items},
        error=result.error,
        latency_ms=result.latency_ms,
    )


async def execute_tool(
    name: str,
    db: AsyncSession,
    *,
    query: str,
    user: User | None = None,
    book_id: int | None = None,
) -> ToolInvocation:
    started = time.perf_counter()
    try:
        if name == "search_books":
            invocation = await _run_search_books(db, query, book_id)
        elif name == "book_analysis":
            invocation = await _run_book_analysis(db, query, book_id)
        elif name == "recommend_books":
            invocation = await _run_recommend(db, user, query)
        elif name == "reading_progress":
            invocation = await _run_progress(db, user)
        elif name == "retrieve_content":
            invocation = await _run_retrieve(db, query, book_id)
        elif name.endswith("_data"):
            invocation = await _run_domain(db, name, query)
        else:
            invocation = ToolInvocation(name=name, status="error", error="未知工具")
    except Exception as exc:  # noqa: BLE001 - 工具失败不阻断回答
        logger.warning(
            "tool_failed",
            extra={"extra_fields": {"tool": name, "error": type(exc).__name__}},
        )
        invocation = ToolInvocation(name=name, status="error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
    invocation.latency_ms = invocation.latency_ms or int((time.perf_counter() - started) * 1000)
    return invocation


async def record_tool_calls(
    db: AsyncSession,
    invocations: list[ToolInvocation],
    *,
    session_id: int | None,
    message_id: int | None,
    user_id: int | None,
) -> None:
    if not invocations:
        return
    for item in invocations:
        db.add(
            ToolCall(
                session_id=session_id,
                message_id=message_id,
                user_id=user_id,
                tool=item.name,
                args=item.args,
                status=item.status,
                latency_ms=item.latency_ms,
                result_size=item.result_size,
                error=(item.error or "")[:1000] or None,
            )
        )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
