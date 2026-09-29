import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from app.config import Settings
from app.ingestion.pipeline import IngestPipeline
from tests.support.embedder import FakeEmbedder

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway pgvector Postgres with the real migrations applied."""
    with PostgresContainer("pgvector/pgvector:0.8.1-pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", "head"],
            cwd=BACKEND_DIR,
            check=True,
            env={**os.environ},
        )
        yield url


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(database_url=database_url, storage_dir=tmp_path / "files")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(database_url)
    async with eng.begin() as conn:
        await conn.execute(
            text("TRUNCATE chunks, documents, ingestion_jobs, collection_acl, collections CASCADE")
        )
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def pipeline(
    session_factory: async_sessionmaker[AsyncSession], embedder: FakeEmbedder, settings: Settings
) -> IngestPipeline:
    return IngestPipeline(session_factory, embedder, settings)
