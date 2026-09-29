import uuid
from datetime import UTC, datetime
from typing import Any

from app.retrieval.types import RetrievedChunk


def make_chunk(
    text: str,
    *,
    n: int = 1,
    title: str = "Leave Policy",
    page: int | None = 4,
    section: str | None = "Leave Policy > Carry forward",
    modified: datetime | None = datetime(2025, 3, 1, tzinfo=UTC),
    flags: dict[str, Any] | None = None,
    logit: float | None = 4.0,
    source_uri: str = "upload://hr-leave-policy.pdf",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=n), document_id=uuid.UUID(int=1000 + n),
        collection_id=uuid.UUID(int=9), ordinal=0, text=text, token_count=len(text.split()),
        page_start=page, page_end=page, section_path=section, doc_title=title,
        source_uri=source_uri, source_type="upload", mime_type="application/pdf", doc_version=1,
        source_modified_at=modified, ingested_at=datetime(2025, 4, 1, tzinfo=UTC),
        flags=flags or {}, rerank_logit=logit,
    )  # fmt: skip
