"""SQLAlchemy 模型导出。

导入本模块即注册全部 28 张表，Alembic autogenerate 依赖此副作用。
"""
from app.models.book import Analysis, Book, Chapter, Chunk
from app.models.chat import ChatMessage, ChatSession, CompanionMessage, ToolCall
from app.models.eval import EvalAdjustment, EvalCase, EvalRun, PromptVersion
from app.models.governance import AuditLog, SecurityEvent
from app.models.memory import Memory
from app.models.ops import BootstrapRun, DomainCall, IngestTask
from app.models.reading import Note, PlanSession, Progress, ReadingHistory, ReadingPlan
from app.models.recommend import Feedback, Recommendation
from app.models.user import Profile, ProfileVersion, User

__all__ = [
    "Analysis",
    "AuditLog",
    "Book",
    "BootstrapRun",
    "Chapter",
    "ChatMessage",
    "ChatSession",
    "Chunk",
    "CompanionMessage",
    "DomainCall",
    "EvalAdjustment",
    "EvalCase",
    "EvalRun",
    "Feedback",
    "IngestTask",
    "Memory",
    "Note",
    "PlanSession",
    "Profile",
    "ProfileVersion",
    "Progress",
    "PromptVersion",
    "ReadingHistory",
    "ReadingPlan",
    "Recommendation",
    "SecurityEvent",
    "ToolCall",
    "User",
]
