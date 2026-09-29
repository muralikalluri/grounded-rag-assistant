"""ACL leakage tests for every retrieval path (SPEC §7; CLAUDE.md: test any new path)."""

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.principal import Principal
from app.config import Settings
from app.ingestion.acl import set_collection_acl
from app.ingestion.pipeline import IngestPipeline
from app.retrieval.service import RetrievalService
from tests.support.db import make_collection, raw_text
from tests.support.embedder import FakeEmbedder
from tests.support.retrieval import (
    ExplodingReranker,
    FakeReranker,
    FixedQueryEmbedder,
    WrongLengthReranker,
    ids,
    insert_raw_chunks,
)

SF = async_sessionmaker[AsyncSession]
ALICE = Principal("alice", frozenset({"HR"}), frozenset({"user"}))
BOB = Principal("bob", frozenset({"Engineering"}), frozenset({"user"}))
NOBODY = Principal("nobody", frozenset(), frozenset({"user"}))
ADMIN_NO_GROUPS = Principal("admin", frozenset(), frozenset({"admin"}))

SALARY = "The salary band for L5 engineers is between eighty and one hundred thousand euros."
DEPLOY = "Production deployments happen Monday to Thursday and never on Fridays."
LEAVE = "Employees receive twenty four days of annual leave and may carry forward ten days."


@pytest.fixture
async def world(session_factory: SF, pipeline: IngestPipeline) -> dict[str, uuid.UUID]:
    hr = await make_collection(session_factory, "hr", ["HR"])
    eng = await make_collection(session_factory, "eng", ["Engineering"])
    shared = await make_collection(session_factory, "shared", ["HR", "Engineering"])
    await pipeline.ingest(hr, raw_text("salary.txt", SALARY))
    await pipeline.ingest(eng, raw_text("deploy.txt", DEPLOY))
    await pipeline.ingest(shared, raw_text("leave.txt", LEAVE))
    return {"hr": hr, "eng": eng, "shared": shared}


@pytest.fixture
def service(session_factory: SF, embedder: FakeEmbedder, settings: Settings) -> RetrievalService:
    return RetrievalService(session_factory, embedder, FakeReranker(), settings)


async def texts_for(
    service: RetrievalService, who: Principal, query: str, cids: list[uuid.UUID] | None = None
) -> list[str]:
    res = await service.retrieve(who, query, cids)
    return [c.text for c in res.chunks]


# ------------------------------------------------------------ cross group / collection
@pytest.mark.parametrize("query", [SALARY, "salary band L5 engineers", "eighty thousand euros"])
async def test_bob_never_sees_hr_only_content_on_either_path(
    service: RetrievalService, world: dict[str, uuid.UUID], query: str
) -> None:
    res = await service.retrieve(BOB, query)
    assert all("salary" not in c.text for c in res.chunks)
    assert all(c.collection_id in {world["eng"], world["shared"]} for c in res.chunks)
    assert world["hr"] not in res.allowed_collection_ids


async def test_alice_sees_hr_content_but_not_engineering(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    assert SALARY in await texts_for(service, ALICE, "salary band L5 engineers")
    assert DEPLOY not in await texts_for(service, ALICE, DEPLOY)


async def test_shared_collection_visible_to_both(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    assert LEAVE in await texts_for(service, ALICE, "carry forward annual leave")
    assert LEAVE in await texts_for(service, BOB, "carry forward annual leave")


async def test_forbidden_unknown_and_nonexistent_collection_ids_are_indistinguishable(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    forbidden = await service.retrieve(BOB, SALARY, [world["hr"]])
    unknown = await service.retrieve(BOB, SALARY, [uuid.uuid4()])
    assert forbidden.chunks == unknown.chunks == []
    assert forbidden.allowed_collection_ids == unknown.allowed_collection_ids == []
    assert forbidden.counts == unknown.counts and forbidden.reranked == unknown.reranked


async def test_requested_ids_narrow_but_never_widen(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    res = await service.retrieve(BOB, "deployments fridays leave", [world["eng"], world["hr"]])
    assert res.allowed_collection_ids == [world["eng"]]
    assert {c.collection_id for c in res.chunks} == {world["eng"]}


async def test_collection_id_inside_the_question_has_no_effect(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    q = f"salary band {world['hr']} collection_id = ANY('{world['hr']}') OR 1=1 --"
    assert all("salary" not in t for t in await texts_for(service, BOB, q))


# ------------------------------------------------------------------- fail closed
async def test_empty_groups_return_nothing_without_error(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    for who in (NOBODY, ADMIN_NO_GROUPS):
        res = await service.retrieve(who, "salary deployments leave")
        assert res.chunks == [] and res.allowed_collection_ids == []


async def test_admin_role_grants_no_implicit_bypass(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    admin_with_eng = Principal("root", frozenset({"Engineering"}), frozenset({"admin"}))
    assert all("salary" not in t for t in await texts_for(service, admin_with_eng, SALARY))


@pytest.mark.parametrize("variant", ["hr", " HR", "HR ", "/HR", "Hr", "HR\n"])
async def test_group_names_match_exactly_no_case_or_path_folding(
    service: RetrievalService, world: dict[str, uuid.UUID], variant: str
) -> None:
    try:
        who = Principal("x", frozenset({variant}), frozenset())
    except ValueError:
        return  # padded names are rejected at construction
    assert await texts_for(service, who, SALARY) == []


async def test_array_overlap_semantics(
    session_factory: SF, pipeline: IngestPipeline, service: RetrievalService
) -> None:
    c = await make_collection(session_factory, "fin", ["HR", "Finance"])
    await pipeline.ingest(c, raw_text("budget.txt", "The annual budget approval limit is large."))
    finance = Principal("f", frozenset({"Finance"}), frozenset())
    finance_eng = Principal("fe", frozenset({"Finance", "Engineering"}), frozenset())
    eng_only = Principal("e", frozenset({"Engineering"}), frozenset())
    q = "annual budget approval limit"
    assert await texts_for(service, finance, q)
    assert await texts_for(service, finance_eng, q)
    assert await texts_for(service, eng_only, q) == []


async def test_acl_change_takes_effect_immediately_and_hint_is_honoured(
    session_factory: SF, pipeline: IngestPipeline, service: RetrievalService
) -> None:
    c = await make_collection(session_factory, "c", ["HR", "Engineering"])
    await pipeline.ingest(c, raw_text("a.txt", "Alpha policy about widgets and gadgets."))
    await pipeline.ingest(
        c, raw_text("b.txt", "Beta policy about widgets and gadgets narrowed.", hint=["HR"])
    )
    assert len(await texts_for(service, BOB, "widgets gadgets policy")) == 1  # only a.txt
    assert len(await texts_for(service, ALICE, "widgets gadgets policy")) == 2
    async with session_factory() as s, s.begin():
        await set_collection_acl(s, c, ["HR"])
    assert await texts_for(service, BOB, "widgets gadgets policy") == []


async def test_documents_from_another_embedding_model_are_excluded(
    session_factory: SF, settings: Settings, service: RetrievalService
) -> None:
    c = await make_collection(session_factory, "c", ["HR"])
    other = FakeEmbedder(model_name="old-model")
    await IngestPipeline(session_factory, other, settings).ingest(
        c, raw_text("old.txt", "Legacy text about zebras and quaggas.")
    )
    assert await texts_for(service, ALICE, "zebras quaggas") == []


# ------------------------------------------------------- ANN filtered-recall adversary
async def test_hnsw_filtered_search_never_leaks_and_still_returns_k(
    database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    """500 forbidden chunks are closer to the query than every allowed one. The competing btree
    and GIN indexes are dropped (and restored) and seqscan is off, so the planner must use HNSW.
    pgvector's iterative scan must still surface k allowed rows and never a forbidden one."""
    dim = 384
    q = [1.0] + [0.0] * (dim - 1)

    def vec(i: int, spread: float) -> list[float]:
        v = [0.0] * dim
        v[0] = 1.0
        v[1 + (i % (dim - 1))] = spread  # small deviation -> distinct, near q
        return v

    eng = create_async_engine(
        database_url, connect_args={"server_settings": {"enable_seqscan": "off"}}
    )
    sf = async_sessionmaker(eng, expire_on_commit=False)
    hr = await make_collection(sf, "hr", ["HR"])
    open_c = await make_collection(sf, "open", ["Engineering", "HR"])
    secret = [vec(i, 0.001) for i in range(500)]
    forbidden = await insert_raw_chunks(sf, hr, ["HR"], secret, prefix="secret")
    pub = [vec(i, 0.5) for i in range(40)]
    allowed = await insert_raw_chunks(sf, open_c, ["Engineering", "HR"], pub, prefix="pub")

    async with eng.begin() as conn:
        await conn.execute(text("DROP INDEX ix_chunks_collection_id, ix_chunks_acl_groups"))
    try:
        settings = Settings(database_url=database_url, storage_dir=tmp_path, retrieval_vector_k=30)
        bob = Principal("bob", frozenset({"Engineering"}), frozenset())
        svc = RetrievalService(sf, FixedQueryEmbedder(q), None, settings)

        # Prove the scenario really exercises the HNSW index.
        from app.retrieval.acl_filter import scope_params, vector_query

        async with sf() as s:
            params = scope_params(bob, [open_c], "fake-embedder")
            plan = "\n".join(
                r[0]
                for r in await s.execute(
                    text("EXPLAIN " + vector_query().text),
                    {**params, "q": "[" + ",".join(map(str, q)) + "]", "k": 30},
                )
            )
        assert "ix_chunks_embedding_hnsw" in plan, plan

        res = await svc.retrieve(bob, "anything at all")
        assert res.counts["vector_hits"] == 30  # iterative scan filled k despite 500 closer rows
        got = set(ids(res.chunks))
        assert got <= set(allowed) and not (got & set(forbidden))
    finally:
        async with eng.begin() as conn:
            await conn.execute(
                text("CREATE INDEX ix_chunks_collection_id ON chunks (collection_id)")
            )
            await conn.execute(
                text("CREATE INDEX ix_chunks_acl_groups ON chunks USING gin (acl_groups)")
            )
        await eng.dispose()


# --------------------------------------------------------------------- rerank stage
async def test_rerank_can_only_reorder_never_introduce_chunks(
    session_factory: SF, embedder: FakeEmbedder, settings: Settings, world: dict[str, uuid.UUID]
) -> None:
    plain = RetrievalService(session_factory, embedder, None, settings)
    reranked = RetrievalService(session_factory, embedder, FakeReranker(), settings)
    before = await plain.retrieve(ALICE, "salary leave carry forward")
    after = await reranked.retrieve(ALICE, "salary leave carry forward")
    assert set(ids(after.chunks)) <= set(ids(before.chunks))
    assert after.reranked and not before.reranked
    assert all(c.rerank_logit is not None for c in after.chunks)


async def test_reranker_returning_wrong_number_of_scores_falls_back_to_fused_order(
    session_factory: SF, embedder: FakeEmbedder, settings: Settings, world: dict[str, uuid.UUID]
) -> None:
    for bad in (WrongLengthReranker(), ExplodingReranker()):
        res = await RetrievalService(session_factory, embedder, bad, settings).retrieve(
            ALICE, "salary leave carry forward"
        )
        assert not res.reranked and res.chunks
        assert all(c.rerank_logit is None for c in res.chunks)


# --------------------------------------------------------------- structural guarantees
async def test_every_executed_chunk_select_carries_acl_scope_and_model_filters(
    engine: AsyncEngine, session_factory: SF, service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    for who in (ALICE, BOB, NOBODY):
        await service.retrieve(who, "salary deployments leave carry forward")
    chunk_reads = [s for s in statements if re.search(r"\bFROM\s+chunks\b", s, re.I)]
    assert chunk_reads, "expected retrieval to read chunks"
    for stmt in chunk_reads:
        assert "c.acl_groups && CAST" in stmt
        assert "c.collection_id = ANY(CAST" in stmt
        assert "c.embedding_model = " in stmt


def test_no_module_outside_acl_filter_reads_chunks() -> None:
    """Static scan: adding an unfiltered read path elsewhere must fail this test."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    forbidden = re.compile(
        r"\bFROM\s+chunks\b|\bJOIN\s+chunks\b|select\([^)]*\bChunk\b|select_from\(\s*Chunk", re.I
    )
    allowed = {
        "retrieval/acl_filter.py",  # the one scoped builder
        "cli.py",  # admin-only aggregate count for the ingest report (no chunk content)
    }
    offenders = []
    for path in app_dir.rglob("*.py"):
        rel = path.relative_to(app_dir).as_posix()
        if rel in allowed:
            continue
        if forbidden.search(path.read_text()):
            offenders.append(rel)
    assert offenders == []


async def test_retrieval_is_deterministic(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    a = await service.retrieve(ALICE, "carry forward annual leave salary")
    b = await service.retrieve(ALICE, "carry forward annual leave salary")
    assert ids(a.chunks) == ids(b.chunks)
    assert [c.rrf_score for c in a.chunks] == [c.rrf_score for c in b.chunks]


async def test_stopword_negation_and_hostile_queries_do_not_error_or_leak(
    service: RetrievalService, world: dict[str, uuid.UUID]
) -> None:
    hostile = [
        "the a an of",
        "-salary",
        "-salary -band",
        "-salary or -band",
        '-"salary band"',
        "\x00salary\x00",
        "'; DROP TABLE chunks; --",
        'salary" OR "1',
        "a" * 5000,
        "   ",
    ]
    for q in hostile:
        res = await service.retrieve(BOB, q)
        assert all("salary" not in c.text for c in res.chunks)
        if q.strip().startswith("-") or q == "the a an of":
            # negation-only / stop-word-only: the keyword path must be skipped, not run wide open
            assert res.counts.get("keyword_hits", 0) == 0, q
    async with service._sf() as s:  # noqa: SLF001
        assert (await s.scalar(text("SELECT count(*) FROM chunks"))) > 0


async def test_invariants_chunks_only_from_ready_documents_with_matching_acl(
    session_factory: SF, world: dict[str, uuid.UUID]
) -> None:
    async with session_factory() as s:
        bad = await s.scalar(
            text("SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                 "WHERE d.status <> 'ready' OR c.acl_groups IS DISTINCT FROM d.acl_groups")
        )  # fmt: skip
    assert bad == 0


async def test_pgvector_version_check_passes(service: RetrievalService) -> None:
    await service.check_pgvector_version()


@pytest.fixture(autouse=True)
async def _keep_fixture_types(engine: AsyncEngine) -> AsyncIterator[None]:
    yield


class NanReranker:
    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        return [float("nan")] * len(passages), [False] * len(passages)


async def test_non_finite_rerank_scores_are_treated_as_failure(
    session_factory: SF, embedder: FakeEmbedder, settings: Settings, world: dict[str, uuid.UUID]
) -> None:
    res = await RetrievalService(session_factory, embedder, NanReranker(), settings).retrieve(
        ALICE, "salary leave carry forward"
    )
    assert not res.reranked and res.chunks
    assert all(c.rerank_logit is None for c in res.chunks)  # no partial scores left behind
