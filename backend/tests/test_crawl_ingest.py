# ruff: noqa: E501
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.principal import Principal
from app.config import Settings
from app.ingestion.connectors.netguard import IpPolicy
from app.ingestion.connectors.web import WebCrawler
from app.ingestion.crawl import crawl_into_collection
from app.ingestion.pipeline import IngestPipeline
from app.models import Chunk, Document
from app.retrieval.service import RetrievalService
from tests.support.db import make_collection
from tests.support.embedder import FakeEmbedder
from tests.support.retrieval import FakeReranker
from tests.support.site import LOOPBACK, FakeResolver, Site, page

SF = async_sessionmaker[AsyncSession]
BODY_A = "The Zephyr warranty covers manufacturing defects for thirty six months from purchase."
BODY_B = "Returns of Zephyr units are accepted within fourteen days when the seal is unbroken."


@pytest.fixture
async def site() -> AsyncIterator[Site]:
    s = Site()
    await s.start()
    s.add("/", page("Zephyr Support", "Welcome to Zephyr support.", ["/warranty", "/returns"]))
    s.add("/warranty", page("Warranty", BODY_A))
    s.add("/returns", page("Returns", BODY_B))
    yield s
    await s.stop()


def make_crawler(site: Site, settings: Settings, **cfg: Any) -> WebCrawler:
    async def no_sleep(_: float) -> None:
        return None

    cs = settings.model_copy(update={"crawl_allowed_ports": [site.port], **cfg})
    return WebCrawler(cs, resolver=FakeResolver({"docs.test": ["127.0.0.1"]}), policy=IpPolicy(LOOPBACK),
                      sleep=no_sleep, allowed_ports=[site.port])  # fmt: skip


async def docs(sf: SF) -> dict[str, Document]:
    async with sf() as s:
        return {d.source_uri: d for d in (await s.scalars(select(Document))).all()}


async def test_crawled_pages_are_ingested_with_the_collection_acl_and_citable_urls(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    hr = await make_collection(session_factory, "web", ["HR"])
    summary = await crawl_into_collection(pipeline, session_factory, settings, hr, site.url("/"),
                                          crawler=make_crawler(site, settings))  # fmt: skip
    assert summary["created"] == 3 and summary["pages"] == 3 and summary["truncated"] is False
    by_uri = await docs(session_factory)
    assert set(by_uri) == {site.url(p) for p in ["/", "/warranty", "/returns"]}
    d = by_uri[site.url("/warranty")]
    assert (
        d.source_type == "web"
        and d.status == "ready"
        and d.acl_groups == ["HR"]
        and d.title == "Warranty"
    )
    async with session_factory() as s:
        chunk_acls = {tuple(a) for a in (await s.scalars(select(Chunk.acl_groups))).all()}
        anchors = await s.scalar(
            text("SELECT count(*) FROM chunks WHERE section_path IS NULL AND page_start IS NULL")
        )
    assert chunk_acls == {("HR",)} and anchors == 0


async def test_crawled_content_obeys_the_acl_at_retrieval_time(
    site: Site,
    session_factory: SF,
    pipeline: IngestPipeline,
    settings: Settings,
    embedder: FakeEmbedder,
) -> None:
    hr = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline, session_factory, settings, hr, site.url("/"), crawler=make_crawler(site, settings)
    )
    svc = RetrievalService(session_factory, embedder, FakeReranker(), settings)
    alice = Principal("alice", frozenset({"HR"}), frozenset())
    bob = Principal("bob", frozenset({"Engineering"}), frozenset())
    assert any(
        "warranty" in c.text.lower()
        for c in (await svc.retrieve(alice, "Zephyr warranty months")).chunks
    )
    assert (await svc.retrieve(bob, "Zephyr warranty months")).chunks == []  # crawled != public


async def test_recrawling_an_unchanged_site_creates_nothing_new(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    async with session_factory() as s:
        before = set((await s.scalars(select(Chunk.id))).all())
    again = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert again["skipped"] == 3 and again["created"] == 0 and again["updated"] == 0
    async with session_factory() as s:
        assert set((await s.scalars(select(Chunk.id))).all()) == before


async def test_a_changed_page_is_updated_in_place(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    site.add("/warranty", page("Warranty", BODY_A.replace("thirty six", "forty eight")))
    out = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert out["updated"] == 1 and out["skipped"] == 2
    d = (await docs(session_factory))[site.url("/warranty")]
    assert d.version == 2
    async with session_factory() as s:
        texts = (await s.scalars(select(Chunk.text).where(Chunk.document_id == d.id))).all()
    assert any("forty eight" in t for t in texts) and not any("thirty six" in t for t in texts)


async def test_a_page_that_returns_404_is_marked_stale_and_can_no_longer_be_cited(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    del site.routes[
        "/returns"
    ]  # the page disappears (and is no longer linked once we drop the link)
    site.add("/", page("Zephyr Support", "Welcome to Zephyr support.", ["/warranty"]))
    out = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert out["gone"] == 1
    gone = (await docs(session_factory))[site.url("/returns")]
    assert gone.status == "stale" and "404" in (gone.error or "")
    async with session_factory() as s:
        assert (
            await s.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == gone.id)
            )
            == 0
        )


async def test_stale_pages_come_back_when_they_reappear(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])

    async def crawl() -> dict[str, Any]:
        return await crawl_into_collection(
            pipeline,
            session_factory,
            settings,
            coll,
            site.url("/"),
            crawler=make_crawler(site, settings),
        )

    await crawl()
    saved = site.routes.pop("/returns")
    site.add("/", page("Zephyr Support", "Welcome", ["/warranty"]))
    await crawl()
    assert (await docs(session_factory))[site.url("/returns")].status == "stale"
    site.routes["/returns"] = saved
    site.add("/", page("Zephyr Support", "Welcome", ["/warranty", "/returns"]))
    await crawl()
    assert (await docs(session_factory))[site.url("/returns")].status == "ready"


async def test_a_partial_or_failing_crawl_never_marks_pages_stale(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    # early stop: only one page is fetched, the others are unseen but still alive (200)
    out = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings, crawl_max_pages=1),
    )
    assert out["truncated"] is True and out["gone"] == 0
    # server errors and robots blocks are "unknown", not "gone"
    site.add("/warranty", b"oops", status=500)
    site.routes.pop("/returns")
    site.add(
        "/robots.txt", "User-agent: *\nDisallow: /returns\n", headers={"Content-Type": "text/plain"}
    )
    out2 = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert out2["gone"] == 0
    statuses = {u: d.status for u, d in (await docs(session_factory)).items()}
    assert set(statuses.values()) == {"ready"}


async def test_the_crawl_only_touches_pages_under_the_seed_origin(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    async with session_factory() as s, s.begin():
        s.add(Document(collection_id=coll, source_type="web", source_uri="http://elsewhere.test/page", title="x",
                       mime_type="text/html", checksum_sha256="0" * 64, version=1, acl_groups=["HR"], status="ready"))  # fmt: skip
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert (await docs(session_factory))[
        "http://elsewhere.test/page"
    ].status == "ready"  # never re-checked


def test_source_url_of_a_crawled_citation_is_the_page_url_and_only_http() -> None:
    from app.generation.answer import _source_url
    from tests.support.chunks import make_chunk

    assert (
        _source_url(make_chunk("x", source_uri="https://docs.test/warranty"))
        == "https://docs.test/warranty"
    )
    assert _source_url(make_chunk("x", source_uri="upload://f.pdf")) is None
    assert _source_url(make_chunk("x", source_uri="javascript:alert(1)")) is None


async def test_summary_keeps_crawler_skip_reasons_separate_from_pipeline_counts(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    """Regression: a merged dict once let the pipeline's `skipped` int overwrite the crawler's."""
    site.add(
        "/robots.txt", "User-agent: *\nDisallow: /returns\n", headers={"Content-Type": "text/plain"}
    )
    coll = await make_collection(session_factory, "web", ["HR"])
    out = await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert out["skip_reasons"] == {"robots": 1} and out["skipped"] == 0 and out["created"] == 2


async def test_a_page_that_now_redirects_elsewhere_is_retired_and_the_new_url_is_ingested(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    # /returns moves to /returns-new and is no longer linked from the home page
    site.add("/returns", b"", status=301, headers={"Location": "/returns-new"})
    site.add("/returns-new", page("Returns", BODY_B))
    site.add("/", page("Zephyr Support", "Welcome to Zephyr support.", ["/warranty"]))
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    by_uri = await docs(session_factory)
    assert by_uri[site.url("/returns")].status == "stale"  # the old URL is no longer citable
    assert by_uri[site.url("/returns-new")].status == "ready"


async def test_lookalike_hosts_are_not_treated_as_the_seed_origin(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    coll = await make_collection(session_factory, "web", ["HR"])
    lookalike = f"{site.url('/')[:-1]}.evil.example/page"  # http://docs.test:PORT.evil.example/page
    async with session_factory() as s, s.begin():
        s.add(Document(collection_id=coll, source_type="web", source_uri=lookalike, title="x", mime_type="text/html",
                       checksum_sha256="1" * 64, version=1, acl_groups=["HR"], status="ready"))  # fmt: skip
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    assert (await docs(session_factory))[
        lookalike
    ].status == "ready"  # prefix match would have re-checked it


async def test_marking_stale_waits_for_an_in_flight_ingest_of_the_same_page(
    site: Site, session_factory: SF, pipeline: IngestPipeline, settings: Settings
) -> None:
    import asyncio

    from app.ingestion.crawl import mark_stale

    coll = await make_collection(session_factory, "web", ["HR"])
    await crawl_into_collection(
        pipeline,
        session_factory,
        settings,
        coll,
        site.url("/"),
        crawler=make_crawler(site, settings),
    )
    d = (await docs(session_factory))[site.url("/returns")]
    async with session_factory() as holder, holder.begin():
        # what an in-flight ingest of this page holds (same key as IngestPipeline)
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{coll}:{d.source_uri}"},
        )

        async def stale() -> None:
            async with session_factory() as s, s.begin():
                await mark_stale(s, coll, d.id, d.source_uri, "gone")

        task = asyncio.create_task(stale())
        await asyncio.sleep(0.4)
        assert not task.done()  # blocked behind the ingest
    await asyncio.wait_for(task, 5)
    assert (await docs(session_factory))[site.url("/returns")].status == "stale"
