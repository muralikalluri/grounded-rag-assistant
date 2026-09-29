"""Stage-1 retrieval gate (SPEC §6.5). Stage 2 lives in grounding.validate_answer."""

from dataclasses import dataclass

from app.config import Settings
from app.retrieval.types import RetrievalResult


@dataclass(frozen=True)
class GateDecision:
    proceed: bool
    reason: str | None  # abstain reason when not proceeding
    gate: str  # "passed" | "abstained" | "skipped_no_rerank"
    top_prob: float | None
    second_prob: float | None
    n_chunks: int
    threshold: float


def retrieval_gate(result: RetrievalResult, settings: Settings) -> GateDecision:
    """Abstain without calling the LLM when nothing relevant was retrieved.

    The threshold applies to ``rerank_prob`` (sigmoid of the cross-encoder logit, 0..1). It is
    uncalibrated; M5 tunes it from the golden set. If the reranker was unavailable there is no
    score to gate on: proceed (stage 2 still validates) unless configured to abstain.
    """
    probs = [c.rerank_prob for c in result.chunks if c.rerank_prob is not None]
    top = probs[0] if probs else None
    second = probs[1] if len(probs) > 1 else None
    base = dict(
        top_prob=top, second_prob=second, n_chunks=len(result.chunks),
        threshold=settings.abstain_rerank_threshold,
    )  # fmt: skip
    if not result.chunks:
        return GateDecision(False, "no_retrieval", "abstained", **base)  # type: ignore[arg-type]
    if not result.reranked:
        if settings.abstain_when_rerank_unavailable:
            return GateDecision(False, "rerank_unavailable", "abstained", **base)  # type: ignore[arg-type]
        return GateDecision(True, None, "skipped_no_rerank", **base)  # type: ignore[arg-type]
    if top is None or top < settings.abstain_rerank_threshold:
        return GateDecision(False, "low_relevance", "abstained", **base)  # type: ignore[arg-type]
    return GateDecision(True, None, "passed", **base)  # type: ignore[arg-type]
