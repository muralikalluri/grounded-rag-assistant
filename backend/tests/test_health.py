from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.health import Probe, get_probes
from app.config import Settings
from app.main import app


async def _ok(_: Settings) -> None:
    return None


async def _fail(_: Settings) -> None:
    raise ConnectionError("secret-host:5432 refused")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _set_probes(probes: dict[str, Probe]) -> None:
    app.dependency_overrides[get_probes] = lambda: probes


async def test_health_is_ok_without_dependencies(client: AsyncClient) -> None:
    _set_probes({"postgres": _fail})
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_ok_when_all_dependencies_up(client: AsyncClient) -> None:
    _set_probes({"postgres": _ok, "redis": _ok})
    r = await client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_ready_503_and_no_error_leak_when_dependency_down(client: AsyncClient) -> None:
    _set_probes({"postgres": _ok, "redis": _fail})
    r = await client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"postgres": "ok", "redis": "unavailable"}
    assert "secret-host" not in r.text
