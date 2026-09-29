import json
from pathlib import Path

import pytest
from eval import readme_results as RR

from tests.conftest import REPO_ROOT


def _report(mode: str, started: str, **metrics: object) -> dict[str, object]:
    rate = {"k": 19, "n": 20, "rate": 0.95, "wilson95": [0.76, 0.99]}
    return {
        "manifest": {
            "run_id": f"r-{started}",
            "started_at": started,
            "mode": mode,
            "git": {"sha": "abc1234", "dirty": False},
            "config": {"embedding_model": "e", "rerank_model": "r", "rerank_top_n": 6},
        },
        "metrics": {
            "n_questions": 2,
            "recall_file": rate,
            "recall_page": rate,
            "acl_leaked_chunks": 0,
            "chunks_checked_for_acl": 12,
            "forbidden_source_hits": 0,
            **metrics,
        },
        "per_question": [{"category": "answerable"}, {"category": "acl_denied"}],
    }


def test_retrieval_only_states_generation_not_run() -> None:
    text = RR.render(_report("retrieval_only", "2026-01-01"), "eval/reports/x.json")  # type: ignore[arg-type]
    assert "Generation metrics have not been run" in text
    assert "recall@6" in text and "19/20" in text and "1 acl-denied" in text


def test_latest_prefers_llm_run_and_skips_test_mode(tmp_path: Path) -> None:
    for name, mode, ts in [
        ("a", "retrieval_only", "2026-03-01"),
        ("b", "llm", "2026-01-01"),
        ("c", "test_mode", "2026-05-01"),
    ]:
        (tmp_path / f"{name}.json").write_text(json.dumps(_report(mode, ts)))
    found = RR.latest_report(tmp_path)
    assert found is not None and found[0].name == "b.json"


def test_replace_block_requires_markers() -> None:
    with pytest.raises(ValueError):
        RR.replace_block("no markers", "x")
    assert (
        RR.replace_block(f"a\n{RR.START}\nold\n{RR.END}\nb", "new")
        == f"a\n{RR.START}\nnew\n{RR.END}\nb"
    )


def test_committed_docs_match_the_newest_report() -> None:
    """README and the handover must be regenerated (`make docs`) after any new report."""
    assert RR.ROOT == REPO_ROOT
    assert RR.main(["--check"]) == 0
