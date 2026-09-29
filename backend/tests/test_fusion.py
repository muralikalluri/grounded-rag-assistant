import uuid
from datetime import UTC, datetime

from app.retrieval.fusion import rrf_fuse
from app.retrieval.types import RetrievedChunk


def chunk(n: int, dist: float | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=n), document_id=uuid.UUID(int=0), collection_id=uuid.UUID(int=0),
        ordinal=n, text=f"t{n}", token_count=1, page_start=None, page_end=None, section_path="s",
        doc_title="d", source_uri="u", source_type="upload", mime_type="text/plain", doc_version=1,
        source_modified_at=None, ingested_at=datetime(2025, 1, 1, tzinfo=UTC), vector_dist=dist,
    )  # fmt: skip


def test_rrf_score_is_sum_of_reciprocal_ranks() -> None:
    v = [chunk(1), chunk(2), chunk(3)]
    k = [chunk(3), chunk(1)]
    fused = rrf_fuse(v, k, 60)
    scores = {c.chunk_id.int: c.rrf_score for c in fused}
    assert abs(scores[1] - (1 / 61 + 1 / 62)) < 1e-12
    assert abs(scores[3] - (1 / 63 + 1 / 61)) < 1e-12
    assert abs(scores[2] - 1 / 62) < 1e-12
    assert [c.chunk_id.int for c in fused] == [1, 3, 2]


def test_dedup_by_chunk_id_keeps_both_ranks() -> None:
    fused = rrf_fuse([chunk(1), chunk(2)], [chunk(2), chunk(1)], 60)
    assert len(fused) == 2
    two = next(c for c in fused if c.chunk_id.int == 2)
    assert (two.vector_rank, two.keyword_rank) == (2, 1)


def test_one_path_empty_returns_other_path_in_order() -> None:
    assert [c.chunk_id.int for c in rrf_fuse([chunk(5), chunk(4)], [], 60)] == [5, 4]
    assert [c.chunk_id.int for c in rrf_fuse([], [chunk(7), chunk(6)], 60)] == [7, 6]
    assert rrf_fuse([], [], 60) == []


def test_ties_break_deterministically() -> None:
    # 1 is rank 1 in vector only; 2 is rank 1 in keyword only -> equal RRF score.
    a = rrf_fuse([chunk(1, dist=0.1)], [chunk(2)], 60)
    b = rrf_fuse([chunk(1, dist=0.1)], [chunk(2)], 60)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert a[0].chunk_id.int == 1  # smaller distance beats "no distance"


def test_k_is_configurable() -> None:
    (c,) = rrf_fuse([chunk(1)], [], 10)
    assert abs(c.rrf_score - 1 / 11) < 1e-12


def test_rerank_prob_is_a_stable_sigmoid() -> None:
    c = chunk(1)
    assert c.rerank_prob is None
    for logit, expected in [(0.0, 0.5), (50.0, 1.0), (-50.0, 0.0), (-5000.0, 0.0), (5000.0, 1.0)]:
        c.rerank_logit = logit
        assert c.rerank_prob is not None and abs(c.rerank_prob - expected) < 1e-9
