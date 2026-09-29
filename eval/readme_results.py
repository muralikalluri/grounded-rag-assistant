# ruff: noqa: E501  (Markdown table templates; long lines are intentional)
"""Fill the results blocks in README.md and sample-deliverable/POC_HANDOVER.md from the newest
committed eval report JSON, so no number in the docs is typed by hand.

    python -m eval.readme_results          # rewrite the blocks
    python -m eval.readme_results --check  # exit 1 if a doc is out of date (used by a test)

A block is the text between ``<!-- results:start -->`` and ``<!-- results:end -->``.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval.report import TARGETS, _fmt_ms, _fmt_rate, target_status

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "eval" / "reports"
DOCS = [ROOT / "README.md", ROOT / "sample-deliverable" / "POC_HANDOVER.md"]
START, END = "<!-- results:start -->", "<!-- results:end -->"

# Prefer a full run over a retrieval-only one; within a mode, the most recently started.
_MODE_RANK = {"llm": 1, "retrieval_only": 0}


def latest_report(reports_dir: Path = REPORTS) -> tuple[Path, dict[str, Any]] | None:
    best: tuple[tuple[int, str], Path, dict[str, Any]] | None = None
    for p in reports_dir.glob("*.json"):
        r = json.loads(p.read_text())
        m = r["manifest"]
        if m["mode"] == "test_mode":
            continue
        key = (_MODE_RANK.get(m["mode"], 0), m["started_at"])
        if best is None or key > best[0]:
            best = (key, p, r)
    return (best[1], best[2]) if best else None


def render(result: dict[str, Any], report_path: str) -> str:
    m, x = result["manifest"], result["metrics"]
    k = m["config"]["rerank_top_n"]
    env = m.get("environment", {})
    git = m["git"]
    cats: dict[str, int] = {}
    for q in result["per_question"]:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    mix = " / ".join(f"{n} {c.replace('_', '-')}" for c, n in cats.items())
    out = [
        f"_Generated from [`{report_path}`]({report_path}) by `make docs`; do not edit by hand._",
        "",
        f"Run `{m['run_id']}`, mode `{m['mode']}`, {x['n_questions']} questions "
        f"({mix}), git `{git['sha']}`"
        f"{' (dirty working tree)' if git['dirty'] else ''}, "
        f"embeddings `{m['config']['embedding_model']}`, reranker `{m['config']['rerank_model']}`.",
        "",
    ]
    if env:
        out += [
            f"Environment: Python {env.get('python')}, torch {env.get('torch')}, device `{env.get('device')}`.",
            "",
        ]
    if m["mode"] == "retrieval_only":
        out += [
            "> **Generation metrics have not been run.** No LLM key was available when this report "
            "was produced, so answer correctness, citation precision and abstention accuracy are "
            "not measured yet. Run `make eval EVAL_ARGS=--yes` with a key to fill them in.",
            "",
        ]
    out += ["| Metric | Result |", "|---|---|"]
    out += [
        f"| Retrieval recall@{k}, file level | {_fmt_rate(x['recall_file'])} |",
        f"| Retrieval recall@{k}, page level (PDF) | {_fmt_rate(x['recall_page'])} |",
        f"| Chunks returned that the asking user may not read | **{x['acl_leaked_chunks']}** "
        f"of {x['chunks_checked_for_acl']} |",
        f"| Forbidden documents retrieved (ACL-denied questions) | **{x['forbidden_source_hits']}** |",
    ]
    if m["mode"] != "retrieval_only":
        c = x["correctness"]
        frac = "n/a" if c["macro_fact_fraction"] is None else f"{c['macro_fact_fraction']:.2f}"
        out += [
            f"| Answer correctness (macro fact fraction) | {frac} over {c['n_answered']} answers |",
            f"| False abstention | {_fmt_rate(x['false_abstention'])} |",
            f"| Abstention accuracy, unanswerable + ACL-denied | {_fmt_rate(x['abstention_combined'])} |",
            f"| Citation precision | {_fmt_rate(x.get('citation_precision'))} |",
            f"| Latency p50 / p95 | {_fmt_ms(x['latency_ms']['p50'])} / {_fmt_ms(x['latency_ms']['p95'])} |",
        ]
    out += ["", "| SPEC §9.4 target | Goal | Result | Status |", "|---|---|---|---|"]
    for key, (label, target, op) in TARGETS.items():
        met = x.get(key)
        out += [
            f"| {label} | {op} {target:.2f} | {_fmt_rate(met)} | {target_status(met, target, op)} |"
        ]
    out += [
        "",
        f"Status uses the 95% Wilson interval, not the point estimate: at {x['n_questions']} questions most targets "
        "cannot be demonstrated either way.",
    ]
    return "\n".join(out)


def replace_block(doc: str, body: str) -> str:
    i, j = doc.find(START), doc.find(END)
    if i < 0 or j < i:
        raise ValueError(f"missing {START} ... {END} markers")
    return doc[: i + len(START)] + "\n" + body + "\n" + doc[j:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    found = latest_report()
    if found is None:
        print("no committed report in eval/reports", file=sys.stderr)
        return 1
    path, result = found
    body = render(result, path.relative_to(ROOT).as_posix())
    stale = []
    for doc in DOCS:
        text = doc.read_text()
        new = replace_block(text, body)
        if new != text:
            stale.append(doc.name)
            if not args.check:
                doc.write_text(new)
    if args.check and stale:
        print(f"out of date: {', '.join(stale)} (run `make docs`)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
