import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingestion.jobs import create_job
from app.ingestion.pipeline import IngestPipeline
from app.models import Chunk, IngestionJob
from app.worker import ingest_document, verify_embedding_setup
from tests.support.db import SENTENCES, make_collection
from tests.support.embedder import FakeEmbedder

SF = async_sessionmaker[AsyncSession]


async def test_startup_check_passes_when_model_config_and_db_agree(
    engine: AsyncEngine, settings: Settings
) -> None:
    await verify_embedding_setup(engine, FakeEmbedder(dim=384), settings)


async def test_startup_check_refuses_model_config_mismatch(
    engine: AsyncEngine, settings: Settings
) -> None:
    with pytest.raises(RuntimeError, match="model dim 10"):
        await verify_embedding_setup(engine, FakeEmbedder(dim=10), settings)


async def test_startup_check_refuses_db_column_mismatch(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    cfg = Settings(database_url=database_url, storage_dir=tmp_path, embedding_dim=768)
    with pytest.raises(RuntimeError, match="database vector column dim 384"):
        await verify_embedding_setup(engine, FakeEmbedder(dim=768), cfg)


def staging(settings: Settings, name: str) -> Path:
    d = settings.storage_dir / "staging"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def ctx_for(sf: SF, pipeline: IngestPipeline, settings: Settings) -> dict[str, object]:
    return {"session_factory": sf, "pipeline": pipeline, "settings": settings}


async def test_worker_ingests_staged_file_records_job_and_deletes_staged_copy(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings, tmp_path: Path
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=2)
    staged = staging(settings, "staged.txt")
    staged.write_text(SENTENCES)
    ctx = ctx_for(session_factory, pipeline, settings)

    res = await ingest_document(ctx, str(job_id), str(cid), str(staged), "policy.txt")
    assert res["action"] == "created" and res["chunks"] > 0
    assert not staged.exists()

    again = staging(settings, "staged2.txt")
    again.write_text(SENTENCES)
    res2 = await ingest_document(ctx, str(job_id), str(cid), str(again), "policy.txt")
    assert res2["action"] == "skipped"

    async with session_factory() as s:
        job = await s.get(IngestionJob, job_id)
        assert job is not None
        assert job.status == "completed" and job.finished_at is not None
        assert job.stats["created"] == 1 and job.stats["skipped"] == 1
        assert job.stats["processed"] == 2
        n = await s.scalar(select(Chunk.id).limit(1))
        assert n is not None


async def test_worker_records_rejected_upload_as_failed(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings, tmp_path: Path
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=1)
    staged = staging(settings, "x.pdf")
    staged.write_bytes(b"MZ\x90\x00 not a pdf")
    res = await ingest_document(
        ctx_for(session_factory, pipeline, settings), str(job_id), str(cid), str(staged), "x.pdf"
    )
    assert res["action"] == "failed"
    assert not staged.exists()
    async with session_factory() as s:
        job = await s.get(IngestionJob, job_id)
        assert job is not None and job.status == "completed_with_errors"
        assert job.stats["failed"] == 1


async def test_worker_records_unexpected_errors_instead_of_leaving_job_running(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=1)
    staged = staging(settings, "a.txt")
    staged.write_text(SENTENCES)
    missing_collection = str(uuid.uuid4())
    res = await ingest_document(
        ctx_for(session_factory, pipeline, settings),
        str(job_id),
        missing_collection,
        str(staged),
        "a.txt",
    )
    assert res["action"] == "failed"
    async with session_factory() as s:
        job = await s.get(IngestionJob, job_id)
        assert job is not None and job.status == "completed_with_errors"
        assert job.stats["failed"] == 1


async def test_worker_refuses_paths_outside_the_staging_area(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings, tmp_path: Path
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=1)
    victim = tmp_path / "precious.txt"
    victim.write_text("do not touch")
    res = await ingest_document(
        ctx_for(session_factory, pipeline, settings), str(job_id), str(cid), str(victim), "p.txt"
    )
    assert res["action"] == "failed"
    assert victim.read_text() == "do not touch"  # neither read into the index nor deleted


async def test_duplicate_delivery_is_not_double_counted(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=1)
    ctx = ctx_for(session_factory, pipeline, settings)
    for i in range(3):
        f = staging(settings, f"d{i}.txt")
        f.write_text(SENTENCES)
        await ingest_document(ctx, str(job_id), str(cid), str(f), "d.txt")
    async with session_factory() as s:
        job = await s.get(IngestionJob, job_id)
        assert job is not None and job.stats["processed"] == 1 and job.status == "completed"


async def test_naive_modified_at_is_stored_as_utc(
    session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    from app.models import Document

    cid = await make_collection(session_factory, "c", ["HR"])
    job_id = await create_job(session_factory, cid, "upload", total=1)
    f = staging(settings, "t.txt")
    f.write_text(SENTENCES)
    await ingest_document(
        ctx_for(session_factory, pipeline, settings),
        str(job_id),
        str(cid),
        str(f),
        "t.txt",
        "2024-05-05",
    )
    async with session_factory() as s:
        doc = (await s.scalars(select(Document))).one()
    assert doc.source_modified_at is not None
    assert doc.source_modified_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert doc.source_modified_at.isoformat().startswith("2024-05-05T00:00:00")
