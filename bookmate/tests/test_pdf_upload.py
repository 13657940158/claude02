"""PDF 上传的文本提取边界。"""
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.core.errors import AppError
from app.utils.pdf import extract_pdf_text


def test_invalid_pdf_is_rejected() -> None:
    with pytest.raises(AppError) as exc:
        extract_pdf_text(b"not a pdf")

    assert exc.value.code == "PDF_PARSE_FAILED"


def test_blank_pdf_has_no_extractable_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)

    with pytest.raises(AppError) as exc:
        extract_pdf_text(buffer.getvalue())

    assert exc.value.code == "PDF_NO_TEXT"
