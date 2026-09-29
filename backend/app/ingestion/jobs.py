import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.pipeline import IngestOutcome
from app.models import IngestionJob

_COUNTERS = ("created", "updated", "skipped", "failed")


async def create_job(
    session_factory: async_sessionmaker[AsyncSession],
    collection_id: uuid.UUID,
    connector: str,
    total: int,
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        job = IngestionJob(
            collection_id=collection_id,
            connector=connector,
            status="running",
            started_at=datetime.now(UTC),
            stats={
                "total": total,
                "processed": 0,
                "chunks_created": 0,
                **dict.fromkeys(_COUNTERS, 0),
            },
        )
        session.add(job)
        await session.flush()
        return job.id


async def record_result(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID, outcome: IngestOutcome
) -> None:
    """Atomically add one document result to the job; finish the job on the last one."""
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("ingestion job not found")
        stats = dict(job.stats)
        if stats.get("processed", 0) >= stats["total"]:
            return  # duplicate delivery (e.g. Arq re-run): never over-count
        stats[outcome.action] = stats.get(outcome.action, 0) + 1
        stats["processed"] = stats.get("processed", 0) + 1
        stats["chunks_created"] = stats.get("chunks_created", 0) + outcome.chunks_created
        job.stats = stats
        if stats["processed"] >= stats["total"]:
            job.status = "completed_with_errors" if stats["failed"] else "completed"
            job.finished_at = datetime.now(UTC)
