import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

Probe = Callable[[Settings], Awaitable[None]]


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]


async def probe_postgres(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def probe_redis(settings: Settings) -> None:
    client = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


def get_probes() -> dict[str, Probe]:
    return {"postgres": probe_postgres, "redis": probe_redis}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: the process is up. Does not touch dependencies."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    probes: Annotated[dict[str, Probe], Depends(get_probes)],
) -> ReadyResponse:
    """Readiness: dependencies reachable. Errors are logged, not returned."""
    checks: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            await asyncio.wait_for(probe(settings), timeout=settings.readiness_timeout_seconds)
            checks[name] = "ok"
        except Exception:
            logger.warning("readiness probe failed: %s", name, exc_info=True)
            checks[name] = "unavailable"
    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ok" if all_ok else "degraded", checks=checks)
