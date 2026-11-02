"""文本处理：清洗、章节切分、切片、hash、引用格式化。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

_CJK = r"一-鿿"
CHAPTER_RE = re.compile(
    rf"^\s*(?:第\s*([0-9一二三四五六七八九十百零两]+)\s*[章回节卷]|chapter\s+(\d+)|##\s+(.+))\s*[:：.、]?\s*(.*)$",
    re.IGNORECASE,
)
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int | None:
    """把「十二」「二十一」「一百零三」等转成整数；纯数字直接返回。"""
    if text.isdigit():
        return int(text)
    total, section, number = 0, 0, 0
    unit_map = {"十": 10, "百": 100, "千": 1000}
    for ch in text:
        if ch in CN_NUM:
            number = CN_NUM[ch]
        elif ch in unit_map:
            unit = unit_map[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        elif ch == "万":
            total += (section + number) * 10000
            section, number = 0, 0
        else:
            return None
    return total + section + number


def normalize_text(raw: str) -> str:
    """统一换行、去 BOM、合并连续空行、去行尾空白。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    text = unicodedata.normalize("NFC", text)
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip() + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    return sha256_text(text)[:length]


@dataclass
class RawChapter:
    chapter_no: int
    title: str
    content: str


def split_chapters(text: str) -> list[RawChapter]:
    """按「第X章 / Chapter N / ## 标题」切章；无标题时整本作为一章。"""
    lines = text.split("\n")
    chapters: list[RawChapter] = []
    current_title: str | None = None
    buffer: list[str] = []
    counter = 0

    def flush() -> None:
        nonlocal buffer, counter, current_title
        content = "\n".join(buffer).strip()
        if content:
            counter += 1
            chapters.append(
                RawChapter(
                    chapter_no=counter,
                    title=(current_title or f"第 {counter} 部分").strip()[:200],
                    content=content,
                )
            )
        buffer = []

    for line in lines:
        match = CHAPTER_RE.match(line)
        is_heading = bool(match) and len(line.strip()) <= 60
        if is_heading:
            flush()
            label = match.group(1) or match.group(2) or match.group(3) or ""
            number = cn_to_int(label)
            rest = (match.group(4) or "").strip()
            if match.group(3):
                current_title = match.group(3).strip()
            else:
                current_title = f"第{number if number is not None else label}章{(' ' + rest) if rest else ''}"
            continue
        buffer.append(line)
    flush()

    if len(chapters) <= 1 and chapters:
        # 没有章节标记：按约 6000 字切成伪章节，便于计划与引用定位
        only = chapters[0]
        paragraphs = only.content.split("\n\n")
        chunks: list[str] = []
        buf: list[str] = []
        size = 0
        for para in paragraphs:
            # 超长单段（没有空行的长文本）按 6000 字硬切，避免整本只有一章
            if len(para) > 6000:
                if buf:
                    chunks.append("\n\n".join(buf))
                    buf, size = [], 0
                chunks.extend(para[i : i + 6000] for i in range(0, len(para), 6000))
                continue
            buf.append(para)
            size += len(para)
            if size >= 6000:
                chunks.append("\n\n".join(buf))
                buf, size = [], 0
        if buf:
            chunks.append("\n\n".join(buf))
        chapters = [
            RawChapter(chapter_no=i + 1, title=f"第 {i + 1} 部分", content=c)
            for i, c in enumerate(chunks)
        ]
    for idx, chapter in enumerate(chapters, start=1):
        chapter.chapter_no = idx
    return chapters


_SENT_END = re.compile(r"(?<=[。！？!?；;\n])")


def split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_END.split(text)]
    return [p for p in parts if p]


@dataclass
class RawChunk:
    chapter_no: int
    text: str
    char_start: int
    char_end: int
    token_estimate: int


def estimate_tokens(text: str) -> int:
    """中文按 ~1.5 字/token，英文/数字按 ~4 字符/token，混合时取加权估算。"""
    cjk = len(re.findall(f"[{_CJK}]", text))
    other = max(len(text) - cjk, 0)
    return int(cjk / 1.5 + other / 4) + 1


def chunk_chapter(
    chapter: RawChapter,
    max_tokens: int = 512,
    overlap_tokens: int = 80,
    min_tokens: int = 40,
) -> list[RawChunk]:
    """按句子贪心聚合为不超过 max_tokens 的切片，相邻切片保留 overlap_tokens 重叠。"""
    sentences = split_sentences(chapter.content)
    if not sentences:
        return []
    chunks: list[RawChunk] = []
    buf: list[str] = []
    buf_tokens = 0
    cursor = 0

    def emit() -> None:
        nonlocal buf, buf_tokens, cursor
        text = "".join(buf).strip()
        if not text:
            buf, buf_tokens = [], 0
            return
        start = cursor
        end = cursor + len(text)
        chunks.append(
            RawChunk(
                chapter_no=chapter.chapter_no,
                text=text,
                char_start=start,
                char_end=end,
                token_estimate=estimate_tokens(text),
            )
        )
        cursor = end
        # 保留尾部若干句作为重叠
        overlap: list[str] = []
        overlap_count = 0
        for sentence in reversed(buf):
            token = estimate_tokens(sentence)
            if overlap_count + token > overlap_tokens:
                break
            overlap.insert(0, sentence)
            overlap_count += token
        if overlap_count == buf_tokens:
            buf, buf_tokens = [], 0
        else:
            buf, buf_tokens = overlap, overlap_count

    for sentence in sentences:
        token = estimate_tokens(sentence)
        if token > max_tokens:
            # 超长单句按字符硬切
            step = max_tokens * 2
            for i in range(0, len(sentence), step):
                piece = sentence[i : i + step]
                if buf:
                    emit()
                buf = [piece]
                buf_tokens = estimate_tokens(piece)
                emit()
            continue
        if buf_tokens + token > max_tokens and buf:
            emit()
        buf.append(sentence)
        buf_tokens += token
    if buf:
        emit()
    # 丢弃过短尾片（并入上一片），避免噪声点
    if len(chunks) >= 2 and chunks[-1].token_estimate < min_tokens:
        tail = chunks.pop()
        last = chunks[-1]
        last.text = f"{last.text}\n{tail.text}"
        last.char_end = tail.char_end
        last.token_estimate = estimate_tokens(last.text)
    return chunks


def excerpt(text: str, needle: str = "", limit: int = 160) -> str:
    """截取片段：若提供了 needle，则以其所在位置为中心。"""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    pos = text.find(needle[:20]) if needle else -1
    if pos < 0:
        return text[:limit] + "…"
    start = max(pos - limit // 3, 0)
    end = min(start + limit, len(text))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def format_citation(book_title: str, chapter_no: int, chapter_title: str, snippet: str) -> str:
    return f"《{book_title}》第{chapter_no}章 {chapter_title}：{snippet}"
