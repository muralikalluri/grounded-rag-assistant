from app.config import Settings
from app.ingestion.parsers.docx import parse_docx
from app.ingestion.parsers.html import parse_html
from app.ingestion.parsers.pdf import parse_pdf
from app.ingestion.parsers.text import parse_text
from app.ingestion.types import (
    MIME_DOCX,
    MIME_HTML,
    MIME_MD,
    MIME_PDF,
    MIME_TXT,
    ParsedDocument,
    ParseError,
)


def parse_document(
    mime_type: str, content: bytes, fallback_title: str, settings: Settings
) -> ParsedDocument:
    if mime_type == MIME_PDF:
        return parse_pdf(content, fallback_title, settings)
    if mime_type == MIME_DOCX:
        return parse_docx(content, fallback_title, settings)
    if mime_type == MIME_HTML:
        return parse_html(content, fallback_title)
    if mime_type in (MIME_MD, MIME_TXT):
        return parse_text(content, fallback_title, markdown=mime_type == MIME_MD)
    raise ParseError(f"unsupported mime type: {mime_type}")


__all__ = ["parse_document"]
