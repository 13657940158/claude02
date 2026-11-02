"""输入/输出护栏：注入检测、引用校验、敏感内容与长度限制。"""
from __future__ import annotations

import re

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions", re.IGNORECASE),
    re.compile(r"忽略(以上|之前|前面|上述).{0,12}(指令|提示|规则|要求)"),
    re.compile(r"(泄露|输出|告诉我|打印).{0,10}(系统提示|system prompt|开发者指令)"),
    re.compile(r"(你现在是|从现在开始你).{0,20}(不受限制|没有限制|越狱|DAN)"),
    re.compile(r"(绕过|关闭|禁用).{0,10}(安全|审核|过滤|限制)"),
    re.compile(r"BEGIN\s+SYSTEM\s+PROMPT", re.IGNORECASE),
]
SENSITIVE_PATTERNS = [
    (re.compile(r"\b1[3-9]\d{9}\b"), "[手机号]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[身份证]"),
    (re.compile(r"\b\d{16,19}\b"), "[银行卡]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[邮箱]"),
]
CITATION_RE = re.compile(r"\[(\d{1,2})\]")
MAX_QUESTION_CHARS = 2000


class GuardrailResult:
    def __init__(self, ok: bool, reason: str = "", kind: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.kind = kind


def check_input(text: str) -> GuardrailResult:
    if not text or not text.strip():
        return GuardrailResult(False, "问题不能为空", "empty")
    if len(text) > MAX_QUESTION_CHARS:
        return GuardrailResult(False, f"问题过长（上限 {MAX_QUESTION_CHARS} 字）", "too_long")
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(
                False,
                "抱歉，我不能执行这类指令。我们可以继续聊选书、读书或你的阅读计划。",
                "prompt_injection",
            )
    # 明显的越权请求：索要其他用户数据
    if re.search(r"(导出|列出|给我).{0,10}(所有用户|别人|其他用户).{0,10}(邮箱|手机号|密码)", text):
        return GuardrailResult(False, "抱歉，用户隐私数据不能提供。", "privacy")
    return GuardrailResult(True)


def redact(text: str) -> str:
    cleaned = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def extract_citation_ids(answer: str) -> list[int]:
    return sorted({int(match) for match in CITATION_RE.findall(answer)})


def validate_citations(answer: str, context_count: int, max_citations: int = 12) -> tuple[bool, list[int]]:
    """引用编号必须落在 [1, context_count]；允许无引用（纯建议类回答）。"""
    ids = extract_citation_ids(answer)
    if len(ids) > max_citations:
        return False, ids
    if any(i < 1 or i > context_count for i in ids):
        return False, ids
    return True, ids


def strip_invalid_citations(answer: str, context_count: int) -> str:
    def repl(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= context_count else ""

    return CITATION_RE.sub(repl, answer)


def looks_like_refusal(answer: str) -> bool:
    markers = ("资料里没有", "资料中没有", "无法回答", "没有找到", "未找到相关", "不能确定")
    return any(marker in answer for marker in markers)


def truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
