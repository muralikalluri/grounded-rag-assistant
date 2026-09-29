import asyncio

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.models import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    # Tests pass an explicit URL via -x; otherwise use application settings.
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url
    asyncio.run(_run_async(url))


run_migrations_online()
