import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion import pipeline as pipeline_module
from app.ingestion.acl import effective_acl, set_collection_acl
from app.ingestion.pipeline import IngestPipeline
from app.models import Chunk, Document
from tests.support.db import SENTENCES, make_collection, raw_text

SF = async_sessionmaker[AsyncSession]


def test_effective_acl_hint_only_narrows() -> None:
    assert effective_acl(["HR", "Eng"], None) == ["Eng", "HR"]
    assert effective_acl(["HR", "Eng"], ["HR"]) == ["HR"]
    assert effective_acl(["HR"], ["HR", "Finance"]) == ["HR"]  # cannot widen
    assert effective_acl([], ["HR"]) == []


async def _visible(sf: SF, groups: list[str], cid=None) -> int:  # type: ignore[no-untyped-def]
    q = "SELECT count(*) FROM chunks WHERE acl_groups && CAST(:g AS text[])"
    params: dict[str, object] = {"g": groups}
    if cid is not None:
        q += " AND collection_id = :cid"
        params["cid"] = cid
    async with sf() as s:
        return int(await s.scalar(text(q), params) or 0)


async def test_chunk_acl_always_equals_document_acl(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    cid = await make_collection(session_factory, "c", ["HR", "Eng"])
    await pipeline.ingest(cid, raw_text("a.txt", SENTENCES))
    await pipeline.ingest(cid, raw_text("b.txt", SENTENCES, hint=["HR"]))
    async with session_factory() as s:
        mismatched = await s.scalar(
            text("SELECT count(*) FROM chunks c JOIN documents d ON d.id=c.document_id "
                 "WHERE c.acl_groups IS DISTINCT FROM d.acl_groups")
        )  # fmt: skip
    assert mismatched == 0


async def test_removing_a_group_removes_access_immediately(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    cid = await make_collection(session_factory, "c", ["HR", "Eng"])
    await pipeline.ingest(cid, raw_text("a.txt", SENTENCES))
    assert await _visible(session_factory, ["Eng"]) > 0
    async with session_factory() as s, s.begin():
        await set_collection_acl(s, cid, ["HR"])
    assert await _visible(session_factory, ["Eng"]) == 0
    assert await _visible(session_factory, ["HR"]) > 0


async def test_acl_change_never_widens_a_narrowed_document(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    out = await pipeline.ingest(cid, raw_text("a.txt", SENTENCES, hint=["HR"]))
    async with session_factory() as s, s.begin():
        await set_collection_acl(s, cid, ["HR", "Eng"])
    async with session_factory() as s:
        doc = await s.get(Document, out.document_id)
        assert doc is not None and doc.acl_groups == ["HR"]
    assert await _visible(session_factory, ["Eng"]) == 0


async def test_empty_collection_acl_fails_closed(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    cid = await make_collection(session_factory, "c", [])
    await pipeline.ingest(cid, raw_text("a.txt", SENTENCES))
    for groups in (["HR"], ["Eng"], []):
        assert await _visible(session_factory, groups) == 0


async def test_failed_acl_update_rolls_back_everything(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    cid = await make_collection(session_factory, "c", ["HR"])
    await pipeline.ingest(cid, raw_text("a.txt", SENTENCES))
    with pytest.raises(RuntimeError):
        async with session_factory() as s, s.begin():
            await set_collection_acl(s, cid, ["Eng"])
            raise RuntimeError("boom")
    assert await _visible(session_factory, ["HR"]) > 0
    assert await _visible(session_factory, ["Eng"]) == 0


async def test_collection_filter_isolates_collections(
    pipeline: IngestPipeline, session_factory: SF
) -> None:
    hr = await make_collection(session_factory, "hr", ["HR"])
    eng = await make_collection(session_factory, "eng", ["Eng"])
    await pipeline.ingest(hr, raw_text("a.txt", SENTENCES))
    await pipeline.ingest(eng, raw_text("b.txt", SENTENCES))
    assert await _visible(session_factory, ["HR"], eng) == 0
    assert await _visible(session_factory, ["HR"], hr) > 0
    async with session_factory() as s:
        n = await s.scalar(select(text("count(*)")).select_from(Chunk))
    assert n and n > 0


async def test_acl_change_during_inflight_ingest_cannot_leave_stale_acl(
    pipeline: IngestPipeline, session_factory: SF, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingest holds the collection row (FOR SHARE) so a concurrent ACL change waits for it."""
    cid = await make_collection(session_factory, "c", ["HR", "Eng"])
    reached, release = asyncio.Event(), asyncio.Event()
    real_embed = pipeline_module.embed_async

    async def paused_embed(embedder, texts):  # type: ignore[no-untyped-def]
        reached.set()
        await release.wait()
        return await real_embed(embedder, texts)

    monkeypatch.setattr(pipeline_module, "embed_async", paused_embed)

    async def change_acl() -> None:
        async with session_factory() as s, s.begin():
            await set_collection_acl(s, cid, ["HR"])

    ingest = asyncio.create_task(pipeline.ingest(cid, raw_text("a.txt", SENTENCES)))
    await asyncio.wait_for(reached.wait(), 10)
    acl_task = asyncio.create_task(change_acl())
    await asyncio.sleep(0.5)
    assert not acl_task.done()  # blocked behind the in-flight ingest
    release.set()
    await asyncio.gather(ingest, acl_task)

    async with session_factory() as s:
        chunk_acls = {tuple(a) for a in (await s.scalars(select(Chunk.acl_groups))).all()}
        doc_acls = {tuple(a) for a in (await s.scalars(select(Document.acl_groups))).all()}
    assert chunk_acls == {("HR",)} and doc_acls == {("HR",)}
    assert await _visible(session_factory, ["Eng"]) == 0
