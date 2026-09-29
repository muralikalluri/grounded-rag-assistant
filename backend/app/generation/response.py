from typing import Any, Literal

from pydantic import BaseModel


class CitationOut(BaseModel):
    n: int
    chunk_id: str
    document_id: str
    doc_title: str
    page_start: int | None
    page_end: int | None
    section_path: str | None
    quote: str  # the span actually found in the chunk, for highlighting
    char_start: int
    char_end: int
    source_date: str | None
    source_url: str | None  # only http(s) URLs; never a storage path
    flags: list[str]


class SentenceOut(BaseModel):
    text: str
    citations: list[int]  # citation numbers (n)
    flags: list[str] = []


class RelatedOut(BaseModel):
    doc_title: str
    page_start: int | None
    section_path: str | None


class GroundingOut(BaseModel):
    sentences_total: int
    sentences_dropped: int
    abstained_reason: str | None
    drop_reasons: dict[str, int]
    stage1: dict[str, Any]


class ChatResponse(BaseModel):
    request_id: str
    # "llm" for real models; "extractive" is the offline test mode (the UI labels it).
    generator: str = "llm"
    status: Literal["answered", "not_found"]
    message: str | None = None
    sentences: list[SentenceOut] = []
    citations: list[CitationOut] = []
    possibly_related: list[RelatedOut] = []
    grounding: GroundingOut
