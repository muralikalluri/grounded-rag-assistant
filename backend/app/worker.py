"""Arq worker: run with ``arq app.worker.WorkerSettings``."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.ingestion.connectors.upload import UploadConnector, UploadRejected
from app.ingestion.embedding import Embedder, LocalEmbedder
from app.ingestion.jobs import record_result
from app.ingestion.pipeline import IngestOutcome, IngestPipeline
from app.logging import configure_logging

logger = logging.getLogger(__name__)


async def verify_embedding_setup(
    engine: AsyncEngine, embedder: Embedder, settings: Settings
) -> None:
    """Refuse to run if the model, config and DB column disagree on the vector dimension."""
    if embedder.dim != settings.embedding_dim:
        raise RuntimeError(
            f"embedding model dim {embedder.dim} != "
            f"configured embedding_dim {settings.embedding_dim}"
        )
    async with engine.connect() as conn:
        # pgvector stores the declared dimension in atttypmod.
        db_dim = await conn.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
    if db_dim != settings.embedding_dim:
        raise RuntimeError(
            f"database vector column dim {db_dim} != "
            f"configured embedding_dim {settings.embedding_dim}; a new dimension needs a migration"
        )


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    embedder = LocalEmbedder(settings)
    await verify_embedding_setup(engine, embedder, settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx["engine"] = engine
    ctx["settings"] = settings
    ctx["session_factory"] = session_factory
    ctx["pipeline"] = IngestPipeline(session_factory, embedder, settings)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


async def ingest_document(
    ctx: dict[str, Any],
    job_id: str,
    collection_id: str,
    staged_path: str,
    filename: str,
    modified_at: str | None = None,
) -> dict[str, Any]:
    """Ingest one staged upload, then delete the staged copy."""
    settings: Settings = ctx["settings"]
    pipeline: IngestPipeline = ctx["pipeline"]
    staged, staging_root = await asyncio.to_thread(
        lambda: (Path(staged_path).resolve(), (settings.storage_dir / "staging").resolve())
    )
    if not staged.is_relative_to(staging_root):
        # The queue payload is not trusted: never read or delete outside the staging area.
        outcome = IngestOutcome("failed", None, "failed", error="staged path outside staging area")
        await record_result(ctx["session_factory"], uuid.UUID(job_id), outcome)
        return {"action": outcome.action, "status": outcome.status, "chunks": 0}
    try:
        try:
            content = await asyncio.to_thread(staged.read_bytes)
            modified = datetime.fromisoformat(modified_at) if modified_at else None
            if modified is not None and modified.tzinfo is None:
                modified = modified.replace(tzinfo=UTC)
            connector = UploadConnector(
                filename, content, max_bytes=settings.upload_max_bytes, modified_at=modified
            )
            items = [i async for i in connector.list_items(None)]
            raw = await connector.fetch(items[0])
            outcome = await pipeline.ingest(uuid.UUID(collection_id), raw)
        except UploadRejected as exc:
            outcome = IngestOutcome("failed", None, "failed", error=exc.reason)
        except Exception as exc:
            # Never leave the job stuck in "running"; record only the exception type.
            logger.error("ingest_document failed", extra={"error": type(exc).__name__})
            outcome = IngestOutcome("failed", None, "failed", error=type(exc).__name__)
        await record_result(ctx["session_factory"], uuid.UUID(job_id), outcome)
    finally:
        await asyncio.to_thread(staged.unlink, missing_ok=True)
    return {"action": outcome.action, "status": outcome.status, "chunks": outcome.chunks_created}


class WorkerSettings:
    functions = [ingest_document]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Embedding is CPU-bound; keep concurrency low.
    max_jobs = 2
    job_timeout = 600
