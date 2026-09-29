import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import chat as chat_module
from app.api.deps import get_chat_service
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.generation.answer import ChatService
from app.llm.types import LLMError
from app.main import app
from tests.support.chunks import make_chunk
from tests.support.llm import BlockingProvider, FakeProvider, StubRetrieval, answer_json

LEAVE = "Employees may carry forward a maximum of 10 days of unused annual leave."
GOOD = ("You can carry forward up to 10 days.", [("S1", "carry forward a maximum of 10 days")])
DEV = Settings(app_env="test", dev_auth_enabled=True, chat_timeout_seconds=5.0)


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_settings] = lambda: DEV
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


def use_service(provider: Any, chunks: list[Any] | None = None) -> None:
    svc = ChatService(
        StubRetrieval([make_chunk(LEAVE)] if chunks is None else chunks), provider, DEV
    )  # type: ignore[arg-type]
    app.dependency_overrides[get_chat_service] = lambda: svc


BODY = {"question": "How many days can I carry forward?"}
HDR = {"X-Dev-User": "alice"}


async def test_streams_status_events_then_exactly_one_answer(client: AsyncClient) -> None:
    use_service(FakeProvider([answer_json([GOOD])]))
    r = await client.post("/api/chat", json=BODY, headers=HDR)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names == ["status", "status", "status", "answer"]
    assert [d["stage"] for e, d in events if e == "status"] == [
        "retrieving",
        "generating",
        "validating",
    ]
    answer = events[-1][1]
    assert answer["status"] == "answered" and answer["citations"][0]["page_start"] == 4
    assert {d["request_id"] for _, d in events[:-1]} == {answer["request_id"]}


async def test_not_found_is_a_normal_answer_event(client: AsyncClient) -> None:
    use_service(FakeProvider([]), chunks=[])
    events = parse_sse((await client.post("/api/chat", json=BODY, headers=HDR)).text)
    assert [e for e, _ in events][-1] == "answer" and events[-1][1]["status"] == "not_found"
    assert sum(e in ("answer", "error") for e, _ in events) == 1


async def test_provider_outage_yields_an_error_event_not_not_found(client: AsyncClient) -> None:
    use_service(FakeProvider([LLMError("http_503", 503)]))
    events = parse_sse((await client.post("/api/chat", json=BODY, headers=HDR)).text)
    closing = [(e, d) for e, d in events if e in ("answer", "error")]
    assert len(closing) == 1 and closing[0][0] == "error"
    assert closing[0][1]["code"] == "llm_unavailable" and "request_id" in closing[0][1]


async def test_overall_timeout_yields_an_error_event(client: AsyncClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="test", dev_auth_enabled=True, chat_timeout_seconds=0.2
    )
    use_service(BlockingProvider())
    events = parse_sse((await client.post("/api/chat", json=BODY, headers=HDR)).text)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "timeout"


# ------------------------------------------------------------------------- auth
async def test_missing_or_unknown_dev_user_is_401_before_streaming(client: AsyncClient) -> None:
    use_service(FakeProvider([]))
    assert (await client.post("/api/chat", json=BODY)).status_code == 401
    r = await client.post("/api/chat", json=BODY, headers={"X-Dev-User": "mallory"})
    assert r.status_code == 401 and "event-stream" not in r.headers["content-type"]


@pytest.mark.parametrize(
    "cfg",
    [
        {"dev_auth_enabled": False},
        {
            "app_env": "prod",
            "audit_hmac_key": "a-real-secret-value",
            "oidc_issuer": "http://kc/r",
            "oidc_jwks_url": "http://kc/c",
        },
    ],
)
async def test_dev_header_is_ignored_when_dev_auth_is_off(
    client: AsyncClient, cfg: dict[str, Any]
) -> None:
    settings = Settings(**cfg)
    app.dependency_overrides[get_settings] = lambda: settings
    if (
        settings.oidc_issuer
    ):  # prod-like: a real verifier is installed, the dev header must still not work
        from app.auth.oidc import OidcVerifier
        from tests.support.oidc import FakeJwks, Keys

        app.state.oidc = OidcVerifier(settings, transport=FakeJwks(Keys()).transport)
    use_service(FakeProvider([]))
    try:
        assert (await client.post("/api/chat", json=BODY, headers=HDR)).status_code == 401
    finally:
        app.state.oidc = None


def test_prod_requires_a_real_audit_hmac_key() -> None:
    oidc = {"oidc_issuer": "http://kc/r", "oidc_jwks_url": "http://kc/c"}
    with pytest.raises(ValueError, match="audit_hmac_key"):
        Settings(app_env="prod", **oidc)
    Settings(
        app_env="prod",
        audit_hmac_key="a-real-secret-value",
        oidc_issuer="http://kc/r",
        oidc_jwks_url="http://kc/c",
    )


def test_dev_auth_cannot_be_enabled_in_prod() -> None:
    with pytest.raises(ValueError, match="dev_auth_enabled"):
        Settings(app_env="prod", dev_auth_enabled=True)


async def test_validation_errors_are_422_before_streaming(client: AsyncClient) -> None:
    use_service(FakeProvider([]))
    for bad in (
        {},
        {"question": ""},
        {"question": "x" * 2001},
        {"question": "q", "collection_ids": ["nope"]},
    ):
        assert (await client.post("/api/chat", json=bad, headers=HDR)).status_code == 422


async def test_service_not_ready_is_503(client: AsyncClient) -> None:
    app.dependency_overrides.pop(get_chat_service, None)
    app.state.chat_service = None
    assert (await client.post("/api/chat", json=BODY, headers=HDR)).status_code == 503


# ------------------------------------------------------------ disconnect handling
class FakeRequest:
    def __init__(self) -> None:
        self.gone = False

    async def is_disconnected(self) -> bool:
        return self.gone


async def test_client_disconnect_cancels_the_in_flight_llm_call() -> None:
    provider = BlockingProvider()
    svc = ChatService(StubRetrieval([make_chunk(LEAVE)]), provider, DEV)  # type: ignore[arg-type]
    req = FakeRequest()
    principal = Principal("alice", frozenset({"HR"}), frozenset())
    resp = await chat_module.chat(
        chat_module.ChatRequest(question="q"),
        req,
        principal,
        svc,
        DEV,  # type: ignore[arg-type]
    )
    it = resp.body_iterator.__aiter__()
    first = await asyncio.wait_for(it.__anext__(), 5)  # the "retrieving" status event
    assert "retrieving" in str(first)
    await asyncio.wait_for(provider.started.wait(), 5)
    req.gone = True
    await it.aclose()  # what Starlette does when the client goes away
    await asyncio.sleep(0.05)
    assert provider.cancelled  # generation stopped; nobody keeps paying for the call
