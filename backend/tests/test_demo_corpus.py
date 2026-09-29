"""Acceptance criteria from SPEC §5.4, run against the committed demo corpus."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.config import Settings, get_settings
from app.models import Chunk, Document
from tests.conftest import REPO_ROOT
from tests.support.embedder import FakeEmbedder

CORPUS = REPO_ROOT / "demo-corpus"
SF = async_sessionmaker[AsyncSession]


def manifest() -> dict[str, object]:
    return json.loads((CORPUS / "manifest.json").read_text())  # type: ignore[no-any-return]


def test_corpus_has_100_documents_all_present_and_fictional() -> None:
    docs = manifest()["documents"]
    assert len(docs) == 100  # type: ignore[arg-type]
    assert all((CORPUS / d["path"]).is_file() for d in docs)  # type: ignore[index, union-attr]
    assert "fictional" in str(manifest()["company"]).lower()


@pytest.fixture
def demo_env(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path, engine: object
) -> Path:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("DEMO_CORPUS_DIR", str(CORPUS))
    get_settings.cache_clear()
    yield tmp_path  # type: ignore[misc]
    get_settings.cache_clear()


class _FakeLocal(FakeEmbedder):
    def __init__(self, _settings: Settings) -> None:
        super().__init__()


async def test_ingest_criteria_with_fake_embedder(
    demo_env: Path, session_factory: SF, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "LocalEmbedder", _FakeLocal)
    first = await cli.ingest_demo(enqueue=False)
    assert first["actions"] == {"created": 100, "updated": 0, "skipped": 0, "failed": 0}
    # the two image-only PDFs are reported, not crashed and not silently dropped
    assert first["documents_by_status"] == {"ready": 98, "needs_ocr": 2}

    async with session_factory() as s:
        # Criterion 2: every chunk has a page (PDF) or a section_path.
        no_anchor = await s.scalar(
            text("SELECT count(*) FROM chunks WHERE page_start IS NULL AND section_path IS NULL")
        )
        assert no_anchor == 0
        pdf_without_page = await s.scalar(
            text("SELECT count(*) FROM chunks c JOIN documents d ON d.id=c.document_id "
                 "WHERE d.mime_type='application/pdf' AND c.page_start IS NULL")
        )  # fmt: skip
        assert pdf_without_page == 0
        ids_before = set((await s.scalars(select(Chunk.id))).all())
        total_before = len(ids_before)
        assert total_before > 100

    # Criterion 3: re-running with no changes creates zero new chunks (and touches none).
    second = await cli.ingest_demo(enqueue=False)
    assert second["actions"] == {"created": 0, "updated": 0, "skipped": 100, "failed": 0}
    async with session_factory() as s:
        assert set((await s.scalars(select(Chunk.id))).all()) == ids_before
    assert (demo_env / "results" / "ingestion" / "demo_ingest_run.json").is_file()
    assert (demo_env / "results" / "ingestion" / "demo_ingest_rerun.json").is_file()


async def test_demo_acl_and_versioned_policy_metadata(
    demo_env: Path, session_factory: SF, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "LocalEmbedder", _FakeLocal)
    await cli.ingest_demo(enqueue=False)
    async with session_factory() as s:
        acl = dict(
            (
                await s.execute(
                    text(
                        "SELECT d.source_uri, d.acl_groups FROM documents d WHERE d.source_uri IN "
                        "('upload://salary-bands.pdf','upload://coding-standards.md','upload://hr-leave-policy.pdf')"
                    )
                )  # fmt: skip
            ).all()
        )
        assert acl["upload://salary-bands.pdf"] == ["HR"]
        assert acl["upload://coding-standards.md"] == ["Engineering"]
        assert acl["upload://hr-leave-policy.pdf"] == ["Engineering", "HR"]
        # v1 / v2 of the expense policy differ by the source-provided modified date.
        rows = (
            await s.execute(
                select(Document.source_uri, Document.source_modified_at).where(
                    Document.source_uri.like("upload://expense-policy-v%")
                )
            )
        ).all()
        dates = {r[0]: r[1] for r in rows}
        assert dates["upload://expense-policy-v2.pdf"] > dates["upload://expense-policy-v1.pdf"]
        # The leave policy carry-forward rule is anchored on page 4.
        page = await s.scalar(
            select(Chunk.page_start)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.source_uri == "upload://hr-leave-policy.pdf")
            .where(Chunk.text.like("%carry forward a maximum of 10 days%"))
        )
        assert page == 4
        n_docs = await s.scalar(select(func.count()).select_from(Document))
        assert n_docs == 100


async def test_real_embedder_ingests_corpus_under_five_minutes(
    demo_env: Path, session_factory: SF
) -> None:
    """Criterion 1, with the real local model (bge-small). Downloads the model on first use."""
    result = await cli.ingest_demo(enqueue=False)
    assert result["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert result["seconds"] < 300, result  # type: ignore[operator]
    assert result["actions"]["failed"] == 0  # type: ignore[index]
    rerun = await cli.ingest_demo(enqueue=False)
    assert rerun["actions"]["created"] == 0 and rerun["actions"]["updated"] == 0  # type: ignore[index]


async def test_retrieval_smoke_with_real_models(demo_env: Path) -> None:
    """M2: sensible results on demo queries, and no ACL leakage, with the real embedder+reranker."""
    await cli.ingest_demo(enqueue=False)
    summary = await cli.retrieval_smoke()
    assert summary["forbidden_returned_total"] == 0
    misses = [r for r in summary["results"] if r["expect"] and not r["rank_of_expected"]]
    assert misses == [], misses
    assert all(r["page_ok"] in (True, None) for r in summary["results"])
    assert (demo_env / "results" / "retrieval" / "smoke.json").is_file()
