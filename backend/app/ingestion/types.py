from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_HTML = "text/html"
MIME_MD = "text/markdown"
MIME_TXT = "text/plain"


class ParseError(Exception):
    """Raised for corrupt, encrypted or unsafe input. The message must not contain document text."""


@dataclass(frozen=True)
class SourceItem:
    uri: str
    filename: str
    modified_at: datetime | None = None


@dataclass(frozen=True)
class RawDocument:
    item: SourceItem
    content: bytes
    mime_type: str
    source_type: str
    # Optional narrowing hint. The effective ACL is collection ACL ∩ hint, never wider.
    acl_hint: list[str] | None = None


@dataclass(frozen=True)
class Block:
    kind: Literal["heading", "paragraph"]
    text: str
    level: int = 0
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class ParsedDocument:
    title: str
    blocks: list[Block] = field(default_factory=list)
    # True for formats with pages (PDF): chunks are anchored to pages.
    paged: bool = False
    page_count: int = 0
    # Paged document that has pages but no extractable text (scanned image).
    needs_ocr: bool = False
