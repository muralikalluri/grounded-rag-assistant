# ruff: noqa: E501  (Markdown table templates; long lines are intentional)
"""Render eval results to Markdown and decide whether a run may become a committed report.

The Markdown is rendered ONLY from the JSON result, and the diff reads JSON, never Markdown,
so every number in a report traces to a results file."""

import json
from pathlib import Path
from typing import Any

# SPEC §9.4 quality targets (to tune towards; reported honestly).
TARGETS: dict[str, tuple[str, float, str]] = {
    "recall_file": ("Retrieval recall@6 (file level)", 0.90, ">="),
    "abstention_combined": ("Abstention accuracy (unanswerable + ACL-denied)", 0.95, ">="),
    "false_abstention": ("False abstention rate", 0.10, "<="),
    "citation_precision": ("Citation precision", 0.90, ">="),
}


class PromotionRefused(Exception):
    pass


def target_status(metric: dict[str, Any] | None, target: float, op: str) -> str:
    """'met' / 'not met' only when the 95% interval decides it; else say so."""
    if not metric or not metric.get("n"):
        return "not run"
    lo, hi = metric["wilson95"]
    if op == ">=":
        if lo >= target:
            return "met (95% CI clears the target)"
        return "not met (95% CI below the target)" if hi < target else "not demonstrable at this n"
    if hi <= target:
        return "met (95% CI clears the target)"
    return "not met (95% CI above the target)" if lo > target else "not demonstrable at this n"


def assert_promotable(result: dict[str, Any], *, allow_dirty: bool, allow_partial: bool) -> None:
    """The single gate between a run and eval/reports. Test-mode runs can never pass."""
    m = result["manifest"]
    if m["mode"] == "test_mode":
        raise PromotionRefused("test-mode (extractive) results are never written to eval/reports")
    if result["metrics"].get("rerank_unavailable_questions", 0) > 0:
        raise PromotionRefused("the reranker was unavailable for some questions: run is invalid")
    if result.get("incomplete") and not allow_partial:
        raise PromotionRefused("run is incomplete (use --allow-partial to promote with a banner)")
    if m["git"]["dirty"] and not allow_dirty:
        raise PromotionRefused("working tree is dirty (commit first, or pass --allow-dirty)")


def _fmt_rate(m: dict[str, Any] | None) -> str:
    if not m or not m["n"]:
        return "n/a"
    lo, hi = m["wilson95"]
    return f"{m['k']}/{m['n']} ({m['rate']:.2f}) [95% CI {lo:.2f}–{hi:.2f}]"


def _fmt_ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.0f} ms"


def find_previous(reports_dir: Path, result: dict[str, Any]) -> dict[str, Any] | None:
    """Latest committed report with the same dataset and corpus (comparable), not this run."""
    m = result["manifest"]
    best: tuple[str, dict[str, Any]] | None = None
    for p in sorted(reports_dir.glob("*.json")):
        prev = json.loads(p.read_text())
        pm = prev["manifest"]
        if pm["run_id"] == m["run_id"] or pm["mode"] != m["mode"]:
            continue
        comparable = (
            pm["dataset_sha256"] == m["dataset_sha256"]
            and pm["corpus_manifest_sha256"] == m["corpus_manifest_sha256"]
        )
        if comparable and (best is None or pm["started_at"] > best[0]):
            best = (pm["started_at"], prev)  # latest by start time, not by file name
    return best[1] if best else None


def diff(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[str]:
    if prev is None:
        return ["No comparable previous report (same dataset, corpus and mode)."]
    lines: list[str] = []
    pm, cm = prev["manifest"], cur["manifest"]
    for key in ("prompt_sha256", "config", "models_seen", "git"):
        if pm.get(key) != cm.get(key):
            lines.append(
                f"- **{key} changed** since `{pm['run_id']}` (deltas below are not like-for-like)."
            )
    changes = []
    p_out = {r["id"]: r["outcome"] for r in prev["per_question"]}
    for r in cur["per_question"]:
        old = p_out.get(r["id"])
        if old is not None and old != r["outcome"]:
            changes.append(f"- `{r['id']}`: {old} → {r['outcome']}")
    lines.append(f"Compared with `{pm['run_id']}`. Per-question status changes: {len(changes)}")
    lines.extend(changes)
    for key, (label, _, _) in TARGETS.items():
        a, b = prev["metrics"].get(key), cur["metrics"].get(key)
        if a and b and a["n"] and b["n"]:
            disjoint = a["wilson95"][1] < b["wilson95"][0] or b["wilson95"][1] < a["wilson95"][0]
            lines.append(
                f"- {label}: {a['k']}/{a['n']} → {b['k']}/{b['n']}"
                + (" (intervals disjoint)" if disjoint else " (not a significant change)")
            )
    return lines


def render_markdown(result: dict[str, Any], previous: dict[str, Any] | None) -> str:
    m, x = result["manifest"], result["metrics"]
    mode = m["mode"]
    out: list[str] = [f"# Evaluation report `{m['run_id']}`", ""]
    if mode == "test_mode":
        out += [
            "> **TEST MODE — NOT MODEL QUALITY.** Answers came from the offline extractive test",
            "> provider (it quotes the best-matching sentence; it is not a language model). These",
            "> numbers exercise the pipeline only and must not be quoted anywhere as results.",
            "",
        ]
    if mode == "retrieval_only":
        out += [
            "> **Generation metrics: not run (no LLM configured).** Only retrieval and ACL checks below.",
            "",
        ]
    if result.get("incomplete"):
        out += [f"> **INCOMPLETE RUN:** {result.get('incomplete_reason', 'stopped early')}", ""]
    if m["git"]["dirty"]:
        out += ["> Working tree was dirty when this ran (`git`: " + str(m["git"]["sha"]) + ").", ""]
    if x.get("rerank_unavailable_questions"):
        out += ["> **INVALID:** the reranker was unavailable for some questions.", ""]

    out += ["## Run", "", "| | |", "|---|---|"]
    out += [f"| mode | {mode} |", f"| questions | {x['n_questions']} ({m['split']}) |"]
    out += [f"| errors (excluded from all rates) | {x['n_errors']} |"]
    out += [f"| git | `{m['git']['sha']}`{' (dirty)' if m['git']['dirty'] else ''} |"]
    out += [
        f"| dataset sha256 | `{m['dataset_sha256'][:16]}…` |",
        f"| corpus manifest sha256 | `{m['corpus_manifest_sha256'][:16]}…` |",
    ]
    out += [
        f"| embedding / reranker | `{m['config']['embedding_model']}` / `{m['config']['rerank_model']}` |"
    ]
    out += [f"| abstain_rerank_threshold | {m['config']['abstain_rerank_threshold']} |"]
    if mode != "retrieval_only":
        out += [
            f"| generator models seen | {', '.join(f'`{s}`' for s in m['models_seen']) or 'none'} |"
        ]
        out += [f"| judge | {m['judge']} |"]
    out += [""]

    out += ["## Retrieval and access control", "", "| Metric | Value |", "|---|---|"]
    out += [f"| Recall@6, file level (answerable) | {_fmt_rate(x['recall_file'])} |"]
    out += [
        f"| Recall@6, page level (PDF sources with a page anchor) | {_fmt_rate(x['recall_page'])} |"
    ]
    out += [
        f"| Chunks returned that the asking user may not read | **{x['acl_leaked_chunks']}** of {x['chunks_checked_for_acl']} |"
    ]
    out += [
        f"| Forbidden documents retrieved (ACL-denied questions) | **{x['forbidden_source_hits']}** |",
        "",
    ]
    if "stage1_preview" in x:
        out += [
            "### Stage-1 gate preview (top rerank probability below the threshold)",
            "",
            "The threshold was tuned on the **dev** split only, so dev rows are in-sample; "
            "**holdout** rows are the fair read.",
            "",
            "| Split | Category | would abstain at stage 1 |",
            "|---|---|---|",
        ]
        for split in ("holdout", "dev"):
            for cat, v in x["stage1_preview"].get(split, {}).items():
                tag = "holdout" if split == "holdout" else "dev (tuning set)"
                out += [f"| {tag} | {cat} | {v['k']}/{v['n']} |"]
        out += [""]

    if mode != "retrieval_only":
        out += _generation_section(x)

    out += [
        "## Targets (SPEC §9.4)",
        "",
        "| Metric | Target | Result | Status |",
        "|---|---|---|---|",
    ]
    for key, (label, target, op) in TARGETS.items():
        met = x.get(key)
        out += [
            f"| {label} | {op} {target:.2f} | {_fmt_rate(met)} | {target_status(met, target, op)} |"
        ]
    out += [
        "",
        "Status is decided by the 95% Wilson interval, not the point estimate: with this few "
        "questions most targets cannot be demonstrated either way.",
        "",
    ]

    out += [
        "## Per question",
        "",
        "| id | user | category | outcome | file recall | notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in result["per_question"]:
        out += [
            f"| {r['id']} | {r['as_user']} | {r['category']} | {r['outcome']} | {r['recall_file']} | {r['notes']} |"
        ]
    out += ["", "## Change since the previous report", ""] + diff(previous, result) + [""]
    out += [
        "## Notes",
        "",
        "- SPEC §9.1 asks for 60+ questions; the MVP cut (§14) is 30, so intervals are wide.",
        "- Outages and other errors are excluded from every denominator (see `errors`).",
        "- `not_found` caused by truncated/refused/invalid model output counts as a system failure, never as a correct abstention.",
        "",
    ]
    return "\n".join(out)


def _generation_section(x: dict[str, Any]) -> list[str]:
    out = ["## Generation", "", "| Metric | Value |", "|---|---|"]
    c = x["correctness"]
    frac = "n/a" if c["macro_fact_fraction"] is None else f"{c['macro_fact_fraction']:.2f}"
    out += [
        f"| Answer correctness (macro fact fraction, answered answerable) | {frac} over {c['n_answered']} (full {c['full']}, partial {c['partial']}, none {c['none']}) |"
    ]
    out += [f"| Answers that also mention an older superseded value | {c['mentions_stale_fact']} |"]
    out += [
        f"| End to end correct (all facts AND a citation from an expected source; of ALL answerable) | {_fmt_rate(x['end_to_end_correct'])} |",
        f"| False abstention (answerable, wrongly said not found) | {_fmt_rate(x['false_abstention'])} — stage 1: {x['false_abstention_stage1']}, stage 2: {x['false_abstention_stage2']} |",
        f"| Answerable not answered, for ANY reason (includes silent system failures) | {_fmt_rate(x['answerable_not_answered'])} |",
    ]
    out += [f"| Abstention accuracy, unanswerable | {_fmt_rate(x['abstention_unanswerable'])} |"]
    out += [
        f"| Abstention accuracy, ACL-denied | {_fmt_rate(x['abstention_acl_denied'])} (denied facts leaked: {x['abstention_acl_denied']['denied_fact_leaks']}) |"
    ]
    out += [f"| Abstention accuracy, combined | {_fmt_rate(x['abstention_combined'])} |"]
    out += [
        f"| System failures (truncated / refused / invalid output) | answerable {x['answerable_system_failures']}, negatives {x['system_failures_on_negatives']} |"
    ]
    if "citation_precision" in x:
        out += [f"| Citation precision (judge) | {_fmt_rate(x['citation_precision'])} |"]
    else:
        out += ["| Citation precision | not run (no judge configured) |"]
    if "judge_rescues" in x:
        out += [f"| Correctness rescued by the judge | {x['judge_rescues']} |"]
    lat = x["latency_ms"]
    out += [
        f"| Latency p50 / p95 (n={lat['n']}; p95 is ~the 2nd slowest) | {_fmt_ms(lat['p50'])} / {_fmt_ms(lat['p95'])} |"
    ]
    cost = x["cost"]
    if cost["total_usd"] is None:
        out += ["| Cost | unknown (no priced calls) |"]
    else:
        tail = (
            ""
            if cost["complete"]
            else f" — INCOMPLETE: {cost['unpriced_calls']} unpriced call(s) not included"
        )
        out += [
            f"| Cost | ${cost['total_usd']:.4f} over {cost['priced_calls']} priced calls{tail} |"
        ]
    out += [f"| Tokens in / out | {cost['input_tokens']:,} / {cost['output_tokens']:,} |", ""]
    out += [
        "Cost counts only successful calls (calls that failed or were retried are not recorded), "
        "so it is a lower bound. Latency includes retries and time spent waiting for the shared "
        "model lock while other questions run (concurrency is in the run manifest).",
        "",
    ]
    return out
