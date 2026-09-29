# ruff: noqa: E501  (dense test data tables)
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from eval import report as R
from eval.threshold import RULE, choose

from app.config import Settings
from tests.conftest import REPO_ROOT

KEYLIKE = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|sk-ant-|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN"
)


def result(mode: str = "llm", **over: Any) -> dict[str, Any]:
    rate = {"k": 19, "n": 20, "rate": 0.95, "wilson95": [0.76, 0.99]}
    base: dict[str, Any] = {
        "manifest": {
            "run_id": "r1", "started_at": "2026-09-29T00:00:00+00:00", "mode": mode, "split": "all",
            "git": {"sha": "abc1234", "dirty": False}, "dataset_sha256": "d" * 64,
            "corpus_manifest_sha256": "c" * 64, "prompt_sha256": {"answer": "1"}, "models_seen": [],
            "judge": "disabled",
            "config": {"embedding_model": "e", "rerank_model": "r", "abstain_rerank_threshold": 0.19},
        },
        "metrics": {
            "n_questions": 30, "n_errors": 0, "recall_file": rate, "recall_page": rate,
            "acl_leaked_chunks": 0, "chunks_checked_for_acl": 180, "forbidden_source_hits": 0,
            "rerank_unavailable_questions": 0,
        },
        "incomplete": False,
        "per_question": [{"id": "q1", "as_user": "alice", "category": "answerable",
                          "outcome": "answered", "recall_file": "hit", "notes": ""}],
    }  # fmt: skip
    if mode != "retrieval_only":  # runs with a generator also carry generation metrics
        base["metrics"].update(
            outcomes={"answered": 1}, false_abstention=rate, end_to_end_correct=rate,
            answerable_not_answered=rate, false_abstention_stage1=0,
            false_abstention_stage2=0, answerable_system_failures=0, system_failures_on_negatives=0,
            correctness={"macro_fact_fraction": 0.9, "n_answered": 20, "full": 18, "partial": 1,
                         "none": 1, "mentions_stale_fact": 0},
            abstention_unanswerable={**rate, "denied_fact_leaks": 0},
            abstention_acl_denied={**rate, "denied_fact_leaks": 0},
            abstention_combined=rate,
            latency_ms={"n": 30, "p50": 1200.0, "p95": 4100.0},
            cost={"priced_calls": 30, "unpriced_calls": 0, "total_usd": 0.5, "complete": True,
                  "input_tokens": 1000, "output_tokens": 100},
        )  # fmt: skip
    base.update(over)
    return base


def test_target_status_is_decided_by_the_interval_not_the_point_estimate() -> None:
    small = {"k": 4, "n": 4, "rate": 1.0, "wilson95": [0.51, 1.0]}
    assert R.target_status(small, 0.95, ">=") == "not demonstrable at this n"
    big = {"k": 200, "n": 200, "rate": 1.0, "wilson95": [0.98, 1.0]}
    assert R.target_status(big, 0.95, ">=").startswith("met")
    low = {"k": 10, "n": 100, "rate": 0.1, "wilson95": [0.05, 0.17]}
    assert R.target_status(low, 0.95, ">=").startswith("not met")
    assert R.target_status(None, 0.95, ">=") == "not run"
    assert R.target_status(
        {"k": 0, "n": 50, "rate": 0.0, "wilson95": [0.0, 0.07]}, 0.10, "<="
    ).startswith("met")
    assert R.target_status(
        {"k": 20, "n": 50, "rate": 0.4, "wilson95": [0.27, 0.54]}, 0.10, "<="
    ).startswith("not met")


# ------------------------------------------------------------------ promotion gate
def test_test_mode_results_can_never_be_promoted() -> None:
    for kw in ({}, {"allow_dirty": True, "allow_partial": True}):
        with pytest.raises(R.PromotionRefused, match="test-mode"):
            R.assert_promotable(
                result("test_mode"), **{"allow_dirty": False, "allow_partial": False, **kw}
            )


def test_invalid_incomplete_and_dirty_runs_are_refused_unless_explicitly_allowed() -> None:
    R.assert_promotable(result(), allow_dirty=False, allow_partial=False)
    bad = result()
    bad["metrics"]["rerank_unavailable_questions"] = 2
    with pytest.raises(R.PromotionRefused, match="reranker"):
        R.assert_promotable(bad, allow_dirty=True, allow_partial=True)  # invalid, no override
    inc = result(incomplete=True)
    with pytest.raises(R.PromotionRefused, match="incomplete"):
        R.assert_promotable(inc, allow_dirty=False, allow_partial=False)
    R.assert_promotable(inc, allow_dirty=False, allow_partial=True)
    dirty = result()
    dirty["manifest"]["git"]["dirty"] = True
    with pytest.raises(R.PromotionRefused, match="dirty"):
        R.assert_promotable(dirty, allow_dirty=False, allow_partial=False)
    R.assert_promotable(dirty, allow_dirty=True, allow_partial=False)


# ------------------------------------------------------------------------- rendering
def test_banners_label_test_mode_retrieval_only_and_incomplete_runs() -> None:
    md = R.render_markdown(result("test_mode"), None)
    assert "TEST MODE — NOT MODEL QUALITY" in md and "not a language model" in md
    md = R.render_markdown(result("retrieval_only"), None)
    assert "Generation metrics: not run (no LLM configured)" in md and "## Generation" not in md
    md = R.render_markdown(
        result("retrieval_only", incomplete=True, incomplete_reason="cost cap"), None
    )
    assert "INCOMPLETE RUN" in md and "cost cap" in md


def test_report_states_small_sample_caveats_and_never_claims_a_target_it_cannot_show() -> None:
    md = R.render_markdown(result("retrieval_only"), None)
    assert "19/20 (0.95) [95% CI 0.76–0.99]" in md
    assert "not demonstrable at this n" in md and "MVP cut" in md
    assert "| Citation precision | >= 0.90 | n/a | not run |" in md


# ------------------------------------------------------------------------------ diff
def test_previous_report_must_share_dataset_corpus_and_mode(tmp_path: Path) -> None:
    prev = result("retrieval_only")
    prev["manifest"]["run_id"] = "old"
    (tmp_path / "a.json").write_text(json.dumps(prev))
    cur = result("retrieval_only")
    assert R.find_previous(tmp_path, cur)["manifest"]["run_id"] == "old"  # type: ignore[index]
    other_data = copy.deepcopy(cur)
    other_data["manifest"]["dataset_sha256"] = "x" * 64
    assert R.find_previous(tmp_path, other_data) is None
    assert R.find_previous(tmp_path, result("llm")) is None  # different mode
    same = copy.deepcopy(prev)
    assert R.find_previous(tmp_path, same) is None  # never compares a run with itself


def test_diff_lists_status_flips_and_config_changes() -> None:
    prev, cur = result(), result()
    prev["manifest"]["run_id"] = "old"
    cur["per_question"][0]["outcome"] = "abstained_stage2"
    cur["manifest"]["config"] = {**cur["manifest"]["config"], "abstain_rerank_threshold": 0.5}
    text = "\n".join(R.diff(prev, cur))
    assert "`q1`: answered → abstained_stage2" in text and "config changed" in text
    assert R.diff(None, cur) == ["No comparable previous report (same dataset, corpus and mode)."]


# -------------------------------------------------------------- committed artefacts
def test_committed_results_and_reports_contain_no_secrets_and_no_test_mode_output() -> None:
    key = Settings().audit_hmac_key.get_secret_value()
    files = [
        *(REPO_ROOT / "eval" / "reports").glob("*"),
        *(REPO_ROOT / "results" / "eval").rglob("*"),
    ]
    for f in (p for p in files if p.is_file()):
        text = f.read_text()
        assert not KEYLIKE.search(text), f"key-like string in {f}"
        assert key not in text, f"hmac key in {f}"
    for f in (REPO_ROOT / "eval" / "reports").glob("*.json"):
        data = json.loads(f.read_text())
        assert data["manifest"]["mode"] != "test_mode", (
            f"test-mode result committed as a report: {f}"
        )
        assert "TEST MODE" not in (f.with_suffix(".md")).read_text()


# ----------------------------------------------------------------- threshold sweep
def rows(**kv: tuple[str, float]) -> list[dict[str, Any]]:
    return [{"id": k, "category": c, "top_prob": p} for k, (c, p) in kv.items()]


def test_threshold_rule_is_biased_low_and_never_exceeds_the_safe_bound() -> None:
    data = rows(
        a=("answerable", 0.90),
        b=("answerable", 0.60),
        n1=("unanswerable", 0.10),
        n2=("acl_denied", 0.20),
    )
    chosen, table = choose(data, 0.02)
    assert chosen == 0.22  # just above the highest negative (0.20) plus margin
    assert next(r for r in table if r["threshold"] == chosen)["stage1_false_abstentions"] == 0
    # a low answerable score caps the threshold below it even if negatives score higher
    overlap = rows(a=("answerable", 0.15), n=("unanswerable", 0.40))
    chosen, _ = choose(overlap, 0.02)
    assert chosen == 0.13
    assert choose(rows(a=("answerable", 0.5)), 0.02)[0] == 0.48  # no negatives: safe bound only
    assert choose(rows(a=("answerable", 0.0)), 0.02)[0] == 0.0  # never negative


def test_default_threshold_equals_the_committed_sweep_result() -> None:
    sweep = json.loads((REPO_ROOT / "results" / "eval" / "threshold_sweep.json").read_text())
    assert Settings().abstain_rerank_threshold == sweep["chosen"], (
        "abstain_rerank_threshold was edited by hand: run `make tune-threshold`"
    )
    from eval.dataset import file_sha256

    dataset = REPO_ROOT / "eval" / "datasets" / "demo_golden.yaml"
    assert sweep["dataset_sha256"] == file_sha256(dataset), (
        "dataset changed since the sweep: re-run it"
    )
    assert sweep["split"] == "dev" and sweep["rule"] == RULE
    assert all(
        r["category"] in ("answerable", "unanswerable", "acl_denied")
        for r in sweep["dev_top_probs"]
    )


def test_previous_report_is_the_latest_by_start_time_not_by_file_name(tmp_path: Path) -> None:
    older, newer = result("retrieval_only"), result("retrieval_only")
    older["manifest"].update(run_id="older", started_at="2026-01-01T00:00:00+00:00")
    newer["manifest"].update(run_id="newer", started_at="2026-06-01T00:00:00+00:00")
    (tmp_path / "z-older-name-sorts-last.json").write_text(json.dumps(older))
    (tmp_path / "a-newer-name-sorts-first.json").write_text(json.dumps(newer))
    cur = result("retrieval_only")
    assert R.find_previous(tmp_path, cur)["manifest"]["run_id"] == "newer"  # type: ignore[index]


def test_report_shows_the_new_generation_rows_and_honesty_notes() -> None:
    res = result("llm")
    res["metrics"]["end_to_end_correct"] = {
        "k": 15,
        "n": 20,
        "rate": 0.75,
        "wilson95": [0.53, 0.89],
    }
    res["metrics"]["answerable_not_answered"] = {
        "k": 2,
        "n": 20,
        "rate": 0.1,
        "wilson95": [0.03, 0.30],
    }
    md = R.render_markdown(res, None)
    assert "End to end correct" in md and "15/20 (0.75)" in md
    assert (
        "for ANY reason" in md and "lower bound" in md and "waiting for the shared model lock" in md
    )


def test_stage1_preview_separates_tuning_and_holdout_rows() -> None:
    res = result("retrieval_only")
    res["metrics"]["stage1_preview"] = {
        "holdout": {"answerable": {"k": 0, "n": 10}},
        "dev": {"answerable": {"k": 0, "n": 10}},
    }
    md = R.render_markdown(res, None)
    assert (
        "| holdout | answerable | 0/10 |" in md and "| dev (tuning set) | answerable | 0/10 |" in md
    )
    assert "in-sample" in md


def test_env_example_does_not_override_tuned_settings() -> None:
    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    active = {ln.split("=", 1)[0] for ln in lines if ln and not ln.startswith("#") and "=" in ln}
    assert "ABSTAIN_RERANK_THRESHOLD" not in active  # tuned value lives in config.py
