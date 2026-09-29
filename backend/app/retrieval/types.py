import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RetrievedChunk:
    """A chunk plus everything M3/M4 need to cite it. Text is data; never log it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    ordinal: int
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: str | None
    doc_title: str
    source_uri: str
    source_type: str
    mime_type: str
    doc_version: int
    source_modified_at: datetime | None
    ingested_at: datetime
    flags: dict[str, Any] = field(default_factory=dict)
    # Per-stage diagnostics.
    vector_rank: int | None = None
    vector_dist: float | None = None
    keyword_rank: int | None = None
    ts_rank: float | None = None
    rrf_score: float = 0.0
    rerank_logit: float | None = None
    truncated: bool = False

    @property
    def rerank_prob(self) -> float | None:
        """sigmoid(logit). Uncalibrated: thresholds must be tuned from eval results."""
        if self.rerank_logit is None:
            return None
        x = self.rerank_logit
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        e = math.exp(x)  # numerically stable for large negative logits
        return e / (1.0 + e)


@dataclass
class RetrievalResult:
    user_id: str
    allowed_collection_ids: list[uuid.UUID]
    chunks: list[RetrievedChunk]
    # False when the reranker failed or was skipped: callers must not trust ordering as relevance.
    reranked: bool
    counts: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
