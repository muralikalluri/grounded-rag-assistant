import uuid

from app.retrieval.types import RetrievedChunk


def rrf_fuse(
    vector_hits: list[RetrievedChunk], keyword_hits: list[RetrievedChunk], k: int
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion: score = sum(1 / (k + rank)), 1-based ranks, deduped by chunk id.

    Deterministic: ties break on best per-path rank, then vector distance, then chunk id.
    Each input list must already be ordered best-first.
    """
    merged: dict[uuid.UUID, RetrievedChunk] = {}
    for rank, hit in enumerate(vector_hits, start=1):
        hit.vector_rank = rank
        hit.rrf_score += 1.0 / (k + rank)
        merged[hit.chunk_id] = hit
    for rank, hit in enumerate(keyword_hits, start=1):
        existing = merged.get(hit.chunk_id)
        if existing is None:
            merged[hit.chunk_id] = hit
            existing = hit
        existing.keyword_rank = rank
        existing.ts_rank = hit.ts_rank
        existing.rrf_score += 1.0 / (k + rank)

    def sort_key(c: RetrievedChunk) -> tuple[float, int, float, str]:
        ranks = [r for r in (c.vector_rank, c.keyword_rank) if r is not None]
        dist = c.vector_dist if c.vector_dist is not None else float("inf")
        return (-c.rrf_score, min(ranks), dist, str(c.chunk_id))

    return sorted(merged.values(), key=sort_key)
