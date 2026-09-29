"""ACL + behaviour tests for the citation preview read paths (chunk text, locate, page image)."""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.preview import get_preview_service
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.ingestion.pipeline import IngestPipeline
from app.main import app
from app.preview import service as service_module
from app.preview.service import BadRequest, NotFound, PreviewService
from tests.support.builders import make_pdf
from tests.support.db import make_collection, raw_pdf, raw_text
from tests.support.embedder import FakeEmbedder

SF = async_sessionmaker[AsyncSession]
ALICE = Principal("alice", frozenset({"HR"}), frozenset({"user"}))
BOB = Principal("bob", frozenset({"Engineering"}), frozenset({"user"}))
ADMIN = Principal("admin", frozenset(), frozenset({"admin"}))
SENT = "The retention period for shipment records is seven years."
PNG = b"\x89PNG\r\n\x1a\n"


def three_page_pdf() -> bytes:
    # A heading per page -> one chunk per page; the target sentence only exists on page 3.
    return make_pdf(
        [
            [("Section one", 16), ("Filler text for the first page of this document.", 11)],
            [("Section two", 16), ("Filler text for the second page of this document.", 11)],
            [("Section three", 16), (SENT, 11)],
        ]
    )


class World:
    def __init__(
        self, hr: uuid.UUID, doc: uuid.UUID, chunk: uuid.UUID, other_doc: uuid.UUID
    ) -> None:
        self.hr, self.doc, self.chunk, self.other_doc = hr, doc, chunk, other_doc


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    return settings.model_copy(update={"embedding_model": "fake-embedder"})


@pytest.fixture
async def world(session_factory: SF, pipeline: IngestPipeline) -> World:
    hr = await make_collection(session_factory, "hr", ["HR"])
    eng = await make_collection(session_factory, "eng", ["Engineering"])
    out = await pipeline.ingest(hr, raw_pdf("hr-policy.pdf", three_page_pdf()))
    other = await pipeline.ingest(
        eng, raw_text("notes.txt", "Engineering only notes about deploys.")
    )
    async with session_factory() as s:
        chunk = await s.scalar(
            text("SELECT id FROM chunks WHERE document_id = :d AND text LIKE '%retention period%'"),
            {"d": out.document_id},
        )
    assert chunk is not None and out.document_id is not None and other.document_id is not None
    return World(hr, out.document_id, chunk, other.document_id)


@pytest.fixture
def svc(session_factory: SF, cfg: Settings) -> PreviewService:
    return PreviewService(session_factory, cfg)


def span(text_: str, phrase: str) -> tuple[int, int]:
    i = text_.index(phrase)
    return i, i + len(phrase)


# ------------------------------------------------------------------ authorised access
async def test_alice_reads_the_chunk_and_it_is_anchored_on_page_three(
    svc: PreviewService, world: World
) -> None:
    view = await svc.get_chunk(ALICE, world.doc, world.chunk)
    assert view.is_pdf and (view.page_start, view.page_end) == (3, 3)
    assert "retention period" in view.text and view.doc_title


async def test_alice_gets_the_page_png_and_it_is_cached_on_disk(
    svc: PreviewService, world: World, cfg: Settings
) -> None:
    png = await svc.page_png(ALICE, world.doc, world.chunk, 3)
    assert png.startswith(PNG)
    cached = list((cfg.storage_dir / "preview-cache").glob("*-3-*.png"))
    assert len(cached) == 1 and cached[0].read_bytes() == png
    assert await svc.page_png(ALICE, world.doc, world.chunk, 3) == png


async def test_locate_reports_the_page_where_the_quote_is(
    svc: PreviewService, world: World
) -> None:
    view = await svc.get_chunk(ALICE, world.doc, world.chunk)
    start, end = span(view.text, "retention period for shipment records")
    res = await svc.locate(ALICE, world.doc, world.chunk, start, end)
    assert res.found and [s.page for s in res.segments] == [3]
    assert res.segments[0].rects and all(0 <= v <= 1 for r in res.segments[0].rects for v in r)


# ------------------------------------------------- everything else is one indistinguishable 404
@pytest.fixture
def leak_cases(world: World) -> list[tuple[str, Principal, uuid.UUID, uuid.UUID]]:
    return [
        ("bob (other group)", BOB, world.doc, world.chunk),
        ("admin role, no groups", ADMIN, world.doc, world.chunk),
        ("random chunk id", ALICE, world.doc, uuid.uuid4()),
        ("random document id", ALICE, uuid.uuid4(), world.chunk),
        ("chunk of a different document", ALICE, world.other_doc, world.chunk),
    ]


async def test_every_denied_or_invalid_case_is_not_found_on_all_three_paths(
    svc: PreviewService, leak_cases: list[tuple[str, Principal, uuid.UUID, uuid.UUID]]
) -> None:
    for label, who, doc, chunk in leak_cases:
        with pytest.raises(NotFound):
            await svc.get_chunk(who, doc, chunk)
        with pytest.raises(NotFound):
            await svc.locate(who, doc, chunk, 0, 10)
        with pytest.raises(NotFound):
            await svc.page_png(who, doc, chunk, 3)
        assert label  # (label shows up in the failing case's output)


async def test_denied_requests_never_touch_the_filesystem(
    svc: PreviewService,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    leak_cases: list[tuple[str, Principal, uuid.UUID, uuid.UUID]],
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("file I/O before the ACL check passed")

    monkeypatch.setattr(service_module, "safe_pdf_path", boom)
    monkeypatch.setattr(service_module, "render_page_png", boom)
    monkeypatch.setattr(service_module, "locate_quote", boom)
    for _, who, doc, chunk in leak_cases:
        for call in (svc.locate(who, doc, chunk, 0, 10), svc.page_png(who, doc, chunk, 3)):
            with pytest.raises(NotFound):
                await call


async def test_acl_is_checked_before_parameters_so_validation_is_no_oracle(
    svc: PreviewService, world: World
) -> None:
    with pytest.raises(NotFound):  # bob: forbidden wins over the invalid range
        await svc.locate(BOB, world.doc, world.chunk, 5, 2)
    with pytest.raises(BadRequest):  # alice: allowed, so the range error is reported
        await svc.locate(ALICE, world.doc, world.chunk, 5, 2)


async def test_pages_outside_the_cited_chunks_pages_are_not_found(
    svc: PreviewService, world: World
) -> None:
    for page in (1, 2, 4, 999):
        with pytest.raises(NotFound):
            await svc.page_png(ALICE, world.doc, world.chunk, page)


@pytest.mark.parametrize("bad", [(-1, 5), (5, 5), (10, 5), (0, 10**9)])
async def test_untrusted_offsets_are_rejected(
    svc: PreviewService, world: World, bad: tuple[int, int]
) -> None:
    with pytest.raises(BadRequest):
        await svc.locate(ALICE, world.doc, world.chunk, *bad)


async def test_overlong_quote_range_is_rejected(
    svc: PreviewService, world: World, cfg: Settings
) -> None:
    tight = PreviewService(svc._sf, cfg.model_copy(update={"quote_max_chars": 10}))  # noqa: SLF001
    with pytest.raises(BadRequest):
        await tight.locate(ALICE, world.doc, world.chunk, 0, 11)


async def test_chunks_from_another_embedding_model_are_not_served(
    session_factory: SF, cfg: Settings
) -> None:
    hr = await make_collection(session_factory, "hr", ["HR"])
    old = IngestPipeline(session_factory, FakeEmbedder(model_name="old-model"), cfg)
    out = await old.ingest(hr, raw_pdf("old.pdf", three_page_pdf()))
    async with session_factory() as s:
        chunk = await s.scalar(
            text("SELECT id FROM chunks WHERE document_id = :d LIMIT 1"), {"d": out.document_id}
        )
    with pytest.raises(NotFound):
        await PreviewService(session_factory, cfg).get_chunk(ALICE, out.document_id, chunk)  # type: ignore[arg-type]


async def test_non_pdf_sources_serve_text_but_no_page_or_locate(
    svc: PreviewService, session_factory: SF, world: World
) -> None:
    # An engineering-only text document, read by bob.
    async with session_factory() as s:
        chunk = await s.scalar(
            text("SELECT id FROM chunks WHERE document_id = :d LIMIT 1"), {"d": world.other_doc}
        )
    view = await svc.get_chunk(BOB, world.other_doc, chunk)
    assert not view.is_pdf and "deploys" in view.text
    with pytest.raises(NotFound):
        await svc.page_png(BOB, world.other_doc, chunk, 1)
    with pytest.raises(NotFound):
        await svc.locate(BOB, world.other_doc, chunk, 0, 5)


async def test_a_tampered_storage_path_cannot_escape_the_storage_dir(
    svc: PreviewService, session_factory: SF, world: World, cfg: Settings, tmp_path: Path
) -> None:
    secret = tmp_path / "outside.pdf"
    secret.write_bytes(three_page_pdf())
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE documents SET storage_path = :p WHERE id = :d"),
            {"p": "../../" + secret.name, "d": world.doc},
        )
    with pytest.raises(NotFound):
        await svc.page_png(ALICE, world.doc, world.chunk, 3)
    with pytest.raises(NotFound):
        await svc.locate(ALICE, world.doc, world.chunk, 0, 10)


async def test_reingest_replaces_chunks_so_old_citations_become_not_found(
    svc: PreviewService, world: World, session_factory: SF, pipeline: IngestPipeline
) -> None:
    changed = make_pdf([[("Section one", 16), ("Completely different content now.", 11)]])
    await pipeline.ingest(world.hr, raw_pdf("hr-policy.pdf", changed))
    with pytest.raises(NotFound):  # the cited chunk no longer exists: the UI shows "source changed"
        await svc.get_chunk(ALICE, world.doc, world.chunk)


# ---------------------------------------------------------------------------- HTTP layer
@pytest.fixture
async def client(svc: PreviewService, cfg: Settings) -> AsyncIterator[AsyncClient]:
    dev = cfg.model_copy(update={"app_env": "test", "dev_auth_enabled": True})
    app.dependency_overrides[get_settings] = lambda: dev
    app.dependency_overrides[get_preview_service] = lambda: svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


def url(
    world: World, tail: str = "", doc: uuid.UUID | None = None, chunk: uuid.UUID | None = None
) -> str:
    return f"/api/documents/{doc or world.doc}/chunks/{chunk or world.chunk}{tail}"


async def test_http_page_image_headers_and_body(client: AsyncClient, world: World) -> None:
    r = await client.get(url(world, "/page?page=3"), headers={"X-Dev-User": "alice"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG)
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "etag" not in r.headers


async def test_http_requires_authentication_and_treats_all_denials_identically(
    client: AsyncClient, world: World
) -> None:
    for tail in ("", "/locate?start=0&end=5", "/page?page=3"):
        assert (await client.get(url(world, tail))).status_code == 401
        assert (
            await client.get(url(world, tail), headers={"X-Dev-User": "nobody"})
        ).status_code == 401
    ok = await client.get(url(world, "/page?page=3"), headers={"X-Dev-User": "alice"})
    assert ok.status_code == 200  # so the 404s below are not vacuous
    bodies = set()
    denied = [
        client.get(url(world, "/page?page=3"), headers={"X-Dev-User": "bob"}),
        client.get(url(world, "/page?page=3", chunk=uuid.uuid4()), headers={"X-Dev-User": "alice"}),
        client.get(
            url(world, "/page?page=3", doc=world.other_doc), headers={"X-Dev-User": "alice"}
        ),
        client.get(url(world, "/page?page=1"), headers={"X-Dev-User": "alice"}),
        client.get(url(world, "/page?page=99999"), headers={"X-Dev-User": "alice"}),
    ]
    for coro in denied:
        r = await coro
        assert r.status_code == 404 and r.headers["cache-control"] == "private, no-store"
        bodies.add(r.text)
    assert len(bodies) == 1


async def test_http_parameter_validation(client: AsyncClient, world: World) -> None:
    h = {"X-Dev-User": "alice"}
    assert (await client.get(url(world, "/page?page=0"), headers=h)).status_code == 422
    assert (await client.get(url(world, "/page"), headers=h)).status_code == 422
    assert (await client.get(url(world, "/locate?start=9&end=3"), headers=h)).status_code == 422
    assert (
        await client.get(f"/api/documents/not-a-uuid/chunks/{world.chunk}", headers=h)
    ).status_code == 422


async def test_http_chunk_and_locate_json(client: AsyncClient, world: World) -> None:
    h = {"X-Dev-User": "alice"}
    chunk = (await client.get(url(world), headers=h)).json()
    assert chunk["is_pdf"] and chunk["page_start"] == 3 and "storage_path" not in chunk
    start = chunk["text"].index("retention period")
    loc = (
        await client.get(url(world, f"/locate?start={start}&end={start + 16}"), headers=h)
    ).json()
    assert loc["found"] and loc["segments"][0]["page"] == 3
    assert (await client.get(url(world), headers={"X-Dev-User": "bob"})).status_code == 404


# ------------------------------------------------------------- structural guarantees
async def test_the_chunk_access_sql_carries_every_filter(
    engine: AsyncEngine, svc: PreviewService, world: World
) -> None:
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    await svc.get_chunk(ALICE, world.doc, world.chunk)
    reads = [s for s in statements if re.search(r"\bFROM\s+chunks\b", s, re.I)]
    assert reads
    for stmt in reads:
        assert "c.acl_groups && CAST" in stmt and "c.collection_id = ANY(CAST" in stmt
        assert "c.embedding_model =" in stmt
        assert "c.id = " in stmt and "c.document_id = " in stmt
        assert "d.status = 'ready'" in stmt and "d.acl_groups && CAST" in stmt


def test_only_the_scoped_module_reads_chunks_documents_or_storage_paths() -> None:
    """Adding a `session.get(Document, id)` + storage_path read elsewhere must fail this test."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    read_docs = re.compile(
        r"session\.get\(\s*Document|select\([^)]*\bDocument\b|FROM\s+documents\b", re.I
    )
    storage = re.compile(r"\bstorage_path\b")
    docs_ok = {"retrieval/acl_filter.py", "cli.py"}
    docs_ok_prefix = ("ingestion/",)  # the writer side: pipeline, acl, jobs
    storage_ok = {
        "retrieval/acl_filter.py",
        "preview/service.py",
        "preview/render.py",  # pure path-safety helper; reads no database
        "models/document.py",
    }
    offenders = []
    for path in app_dir.rglob("*.py"):
        rel = path.relative_to(app_dir).as_posix()
        src = path.read_text()
        if read_docs.search(src) and rel not in docs_ok and not rel.startswith(docs_ok_prefix):
            offenders.append(f"{rel}: reads documents")
        if storage.search(src) and rel not in storage_ok and not rel.startswith(docs_ok_prefix):
            offenders.append(f"{rel}: touches storage_path")
    assert offenders == []


# ------------------------------------------------------------- concurrency / robustness
async def test_concurrent_cold_page_requests_all_succeed_with_identical_bytes(
    session_factory: SF, cfg: Settings, world: World
) -> None:
    fresh = PreviewService(
        session_factory, cfg.model_copy(update={"preview_render_concurrency": 4})
    )
    results = await asyncio.gather(
        *[fresh.page_png(ALICE, world.doc, world.chunk, 3) for _ in range(8)]
    )
    assert all(r.startswith(PNG) for r in results) and len({r for r in results}) == 1
    leftovers = list((cfg.storage_dir / "preview-cache").glob("*.tmp"))
    assert leftovers == []  # no temp files left behind by racing writers


async def test_a_timed_out_render_keeps_its_slot_until_the_thread_really_finishes(
    session_factory: SF, cfg: Settings, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Threads can't be cancelled: stopping to wait must not free the slot (or renders pile up)."""
    import threading
    import time

    running = 0
    peak = 0
    lock = threading.Lock()

    def slow(*a: object, **k: object) -> bytes:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.6)
        with lock:
            running -= 1
        return PNG

    monkeypatch.setattr(service_module, "render_page_png", slow)
    tight = cfg.model_copy(
        update={"preview_render_concurrency": 1, "preview_render_timeout_seconds": 0.2}
    )
    svc = PreviewService(session_factory, tight)
    outcomes = await asyncio.gather(
        *[svc.page_png(ALICE, world.doc, world.chunk, 3) for _ in range(3)], return_exceptions=True
    )
    await asyncio.sleep(1.5)  # let straggling threads finish
    assert all(isinstance(o, NotFound) for o in outcomes)  # timed out, generic 404
    assert peak == 1  # never more than one render thread at a time
