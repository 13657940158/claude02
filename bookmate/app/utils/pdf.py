"""PDF 文本提取。

只处理带文字层的 PDF；扫描件没有可提取文本，需要先 OCR。
"""
from __future__ import annotations

import io

from pypdf import PdfReader

from app.core.errors import AppError

MIN_TEXT_CHARS = 50


def extract_pdf_text(data: bytes, *, min_chars: int = MIN_TEXT_CHARS) -> str:
    """把 PDF 字节流转成纯文本；失败或无可读文字时抛 AppError。"""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型较多，统一转业务错误
        raise AppError(
            code="PDF_PARSE_FAILED",
            status_code=400,
            message="PDF 解析失败，请确认文件未损坏或未加密",
        ) from exc

    text = "\n\n".join(page for page in pages if page)
    if len(text) < min_chars:
        raise AppError(
            code="PDF_NO_TEXT",
            status_code=400,
            message="PDF 没有可提取的文字（可能是扫描版），请先 OCR 或改用 TXT/MD",
        )
    return text
