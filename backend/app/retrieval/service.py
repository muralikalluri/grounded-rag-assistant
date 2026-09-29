import asyncio
import logging
import math
import time
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.principal import Principal
from app.config import Settings
from app.ingestion.embedding import Embedder
from app.retrieval.acl_filter import (
    keyword_precheck_query,
    keyword_query,
    resolve_scope,
    scope_params,
    vector_query,
)
from app.retrieval.fusion import rrf_fuse
from app.retrieval.rerank import Reranker, score_async
from app.retrieval.types import RetrievalResult, RetrievedChunk

logger = logging.getLogger(__name__)


def _to_chunk(row: Any) -> RetrievedChunk:
    m = row._mapping
    return RetrievedChunk(
        chunk_id=m["chunk_id"],
        document_id=m["document_id"],
        collection_id=m["collection_id"],
        ordinal=m["ordinal"],
        text=m["text"],
        token_count=m["token_count"],
        page_start=m["page_start"],
        page_end=m["page_end"],
        section_path=m["section_path"],
        doc_title=m["doc_title"],
        source_uri=m["source_uri"],
        source_type=m["source_type"],
        mime_type=m["mime_type"],
        doc_version=m["doc_version"],
        source_modified_at=m["source_modified_at"],
        ingested_at=m["ingested_at"],
        flags=dict(m["flags"] or {}),
        vector_dist=float(m["dist"]) if m["dist"] is not None else None,
        ts_rank=float(m["ts_rank"]) if m["ts_rank"] is not None else None,
    )


def clean_query(query: str, max_chars: int) -> str:
    """Strip NULs (asyncpg rejects them), collapse whitespace, cap length."""
    return " ".join(query.replace("\x00", " ").split())[:max_chars]


def to_vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


class RetrievalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        reranker: Reranker | None,
        settings: Settings,
    ) -> None:
        self._sf = session_factory
        self._embedder = embedder
        self._reranker = reranker
        self._settings = settings

    async def check_pgvector_version(self) -> None:
        """Iterative HNSW scans (filtered recall) need pgvector >= 0.8.0."""
        async with self._sf() as session:
            ver = await session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )

        def parse(v: object) -> tuple[int, ...]:
            return tuple(int(x) for x in str(v).split("."))

        if ver is None or parse(ver) < parse(self._settings.pgvector_min_version):
            raise RuntimeError(
                f"pgvector {ver} < required {self._settings.pgvector_min_version} "
                "(needed for filtered HNSW iterative scan)"
            )

    async def retrieve(
        self,
        principal: Principal,
        query: str,
        collection_ids: Sequence[uuid.UUID] | None = None,
    ) -> RetrievalResult:
        s = self._settings
        timings: dict[str, float] = {}
        counts: dict[str, int] = {}
        query = clean_query(query, s.query_max_chars)
        empty = RetrievalResult(principal.user_id, [], [], reranked=False, counts=counts)
        if not query or not principal.groups:
            return empty

        t0 = time.perf_counter()
        qvec = (await asyncio.to_thread(self._embedder.embed, [s.embedding_query_prefix + query]))[
            0
        ]
        timings["embed_query"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        # One read-only snapshot for scope resolution and both paths: they see the same ACLs.
        async with self._sf() as session:
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": True,
                }
            )
            cids = await resolve_scope(session, principal, collection_ids)
            if not cids:
                return empty
            params = scope_params(principal, cids, self._embedder.model_name)
            await session.execute(
                text(
                    "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                    "set_config('hnsw.ef_search', :ef, true), "
                    "set_config('hnsw.max_scan_tuples', :mx, true)"
                ),
                {"ef": str(s.hnsw_ef_search), "mx": str(s.hnsw_max_scan_tuples)},
            )
            vec_rows = (
                await session.execute(
                    vector_query(),
                    {**params, "q": to_vector_literal(qvec), "k": s.retrieval_vector_k},
                )
            ).all()
            kw_rows: Sequence[Any] = []
            pre = (await session.execute(keyword_precheck_query(), {"qtext": query})).one()
            # querytree() is 'T' for negation-only queries ("-a", "-a or -b", '-"a b"'), which would
            # match nearly every chunk; it is empty for stop-word-only queries. Skip both.
            if pre.n > 0 and pre.indexable not in ("T", ""):
                kw_rows = (
                    await session.execute(
                        keyword_query(), {**params, "qtext": query, "k": s.retrieval_keyword_k}
                    )
                ).all()
        timings["sql"] = (time.perf_counter() - t0) * 1000

        vector_hits = [_to_chunk(r) for r in vec_rows]
        keyword_hits = [_to_chunk(r) for r in kw_rows]
        counts["vector_hits"] = len(vector_hits)
        counts["keyword_hits"] = len(keyword_hits)
        if len(vector_hits) < s.retrieval_vector_k:
            logger.info(
                "vector path returned fewer than k",
                extra={"got": len(vector_hits), "k": s.retrieval_vector_k},
            )

        fused = rrf_fuse(vector_hits, keyword_hits, s.rrf_k)[: s.rerank_candidates]
        counts["fused"] = len(fused)

        reranked = False
        final = fused[: s.rerank_top_n]
        if self._reranker is not None and fused:
            t0 = time.perf_counter()
            try:
                logits, truncated = await score_async(
                    self._reranker, query, [c.text for c in fused]
                )
                if len(logits) != len(fused) or len(truncated) != len(fused):
                    raise ValueError("reranker returned a different number of scores")
                if not all(math.isfinite(x) for x in logits):
                    raise ValueError("reranker returned a non-finite score")
                # Assign only after every check passed, so a failure never leaves partial scores.
                for c, logit, trunc in zip(fused, logits, truncated, strict=True):
                    c.rerank_logit, c.truncated = logit, trunc
                # Reorder only the chunks we already have: rerank cannot introduce new ones.
                final = sorted(
                    fused,
                    key=lambda c: (-(c.rerank_logit or 0.0), -c.rrf_score, str(c.chunk_id)),
                )[: s.rerank_top_n]
                reranked = True
                counts["truncated_for_rerank"] = sum(truncated)
            except Exception:
                logger.warning("rerank failed; returning fused order", exc_info=True)
            timings["rerank"] = (time.perf_counter() - t0) * 1000

        counts["returned"] = len(final)
        logger.info(
            "retrieval",
            extra={
                "user": principal.user_id,
                "chunk_ids": [str(c.chunk_id) for c in final],
                "reranked": reranked,
                **counts,
            },
        )
        return RetrievalResult(
            principal.user_id, cids, final, reranked=reranked, counts=counts, timings_ms=timings
        )
