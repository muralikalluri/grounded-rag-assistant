"""Crawl a site into a collection through the normal ingestion pipeline."""

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingestion.connectors.web import CrawlResult, WebCrawler, summarize
from app.ingestion.pipeline import IngestPipeline
from app.ingestion.types import RawDocument
from app.models import Chunk, Document

logger = logging.getLogger(__name__)


async def mark_stale(
    session: AsyncSession, collection_id: uuid.UUID, doc_id: uuid.UUID, uri: str, reason: str
) -> None:
    """The page is gone. Drop its chunks (so nothing can retrieve or cite it) and flag the row.

    Deleting chunks keeps the invariant "chunks exist => document is ready" that the retrieval
    SQL relies on, so no new retrieval path (and no new ACL surface) is needed.
    """
    # Same per-document lock the ingest pipeline takes, so a concurrent re-ingest cannot interleave.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{collection_id}:{uri}"},
    )
    await session.execute(delete(Chunk).where(Chunk.document_id == doc_id))
    await session.execute(
        update(Document).where(Document.id == doc_id).values(status="stale", error=reason)
    )


async def crawl_into_collection(
    pipeline: IngestPipeline,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    collection_id: uuid.UUID,
    seed: str,
    *,
    crawler: WebCrawler | None = None,
) -> dict[str, Any]:
    own = crawler is None
    crawler = crawler or WebCrawler(settings)
    counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "gone": 0}

    async def on_page(raw: RawDocument) -> None:
        out = await pipeline.ingest(collection_id, raw)
        counts[out.action] += 1

    try:
        result: CrawlResult = await crawler.crawl(seed, on_page)
        await _revalidate_missing(
            crawler, pipeline, sf, settings, collection_id, seed, result, counts
        )
    finally:
        if own:
            await crawler.aclose()
    # Prefixed: bare keys like "created" collide with reserved LogRecord attributes.
    logger.info(
        "crawl finished",
        extra={
            "crawl_pages": result.pages,
            "crawl_truncated": result.truncated,
            **{f"crawl_{k}": v for k, v in counts.items()},
        },
    )
    return {**summarize(result), **counts}


async def _revalidate_missing(
    crawler: WebCrawler,
    pipeline: IngestPipeline,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    collection_id: uuid.UUID,
    seed: str,
    result: CrawlResult,
    counts: dict[str, int],
) -> None:
    """Previously ingested pages this crawl did not reach: re-check them individually.

    Only an explicit 404/410 marks a page stale. Unreachable, errors, robots blocks and early
    stops leave it untouched, so a partial or failed crawl can never delete good content.
    """
    seed_parts = urlsplit(seed)
    origin = (seed_parts.scheme.lower(), seed_parts.netloc.lower())
    async with sf() as session:
        rows = (
            await session.execute(
                select(Document.id, Document.source_uri).where(
                    Document.collection_id == collection_id,
                    Document.source_type == "web",
                    Document.status == "ready",
                )
            )
        ).all()

    def same_origin(uri: str) -> bool:  # exact scheme+host+port, not a string prefix
        p = urlsplit(uri)
        return (p.scheme.lower(), p.netloc.lower()) == origin

    missing = [
        (doc_id, uri) for doc_id, uri in rows if uri not in result.seen and same_origin(uri)
    ][: settings.crawl_max_revalidate]
    for doc_id, uri in missing:
        verdict, raw = await crawler.fetch_one(uri)
        if verdict == "gone":
            async with sf() as session, session.begin():
                await mark_stale(
                    session, collection_id, doc_id, uri, "page returned 404/410 in a later crawl"
                )
            counts["gone"] += 1
        elif verdict == "ok" and raw is not None:
            out = await pipeline.ingest(collection_id, raw)  # still alive (just not linked)
            counts[out.action] += 1
            if raw.item.uri != uri:
                # The old URL now redirects elsewhere: the content lives at the new URL (just
                # ingested), so retire the old document instead of leaving it citable forever.
                async with sf() as session, session.begin():
                    await mark_stale(
                        session, collection_id, doc_id, uri, "page moved (redirects to another URL)"
                    )
                counts["gone"] += 1
