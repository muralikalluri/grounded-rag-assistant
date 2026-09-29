"""Tune the stage-1 abstention threshold from DEV-split retrieval scores only (no LLM needed).

Rule (fixed before the holdout split was ever evaluated; revised once after seeing the dev
score distribution, see RULE): a stage-1 false abstention loses an answer for good, while a
missed stage-1 catch is still caught by stage-2 grounding, so the threshold is biased LOW: the
lower of (a) the highest zero-false-abstention threshold minus a margin and (b) just above the
highest dev negative plus the margin. The holdout split is never looked at here. The result is
written to results/eval/threshold_sweep.json; ``--apply`` rewrites the default in app/config.py
from that file, and a test asserts the two agree, so hand edits fail CI.
"""

import argparse
import asyncio
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.principal import dev_principal
from app.config import get_settings
from app.ingestion.embedding import LocalEmbedder
from app.retrieval.rerank import CrossEncoderReranker
from app.retrieval.service import RetrievalService
from eval.dataset import file_sha256, load_dataset

REPO = Path(__file__).resolve().parent.parent
SWEEP = REPO / "results" / "eval" / "threshold_sweep.json"
CONFIG = REPO / "backend" / "app" / "config.py"
RULE = (
    "lower of: (a) highest 0.01-grid threshold with 0 stage-1 false abstentions on dev "
    "answerable questions minus eval_threshold_margin; (b) the highest dev negative score "
    "rounded up to the grid plus eval_threshold_margin. Biased low because a stage-1 false "
    "abstention is unrecoverable and a missed catch is handled by stage 2. (Rule revised once, "
    "after seeing dev scores and before any holdout evaluation: the original 'highest safe "
    "threshold' sat on the weakest dev answerable question.)"
)


def choose(top_probs: list[dict[str, Any]], margin: float) -> tuple[float, list[dict[str, Any]]]:
    """Pure: from per-question top rerank probabilities to (chosen threshold, sweep table)."""
    grid = [round(i / 100, 2) for i in range(0, 96)]
    table = []
    for t in grid:
        fa = sum(1 for r in top_probs if r["category"] == "answerable" and r["top_prob"] < t)
        catches = sum(1 for r in top_probs if r["category"] != "answerable" and r["top_prob"] < t)
        table.append({"threshold": t, "stage1_false_abstentions": fa, "stage1_catches": catches})
    safe = [row["threshold"] for row in table if row["stage1_false_abstentions"] == 0]
    upper = max(0.0, round((max(safe) if safe else 0.0) - margin, 2))
    negatives = [r["top_prob"] for r in top_probs if r["category"] != "answerable"]
    if not negatives:
        return upper, table
    just_above = round(math.ceil(max(negatives) * 100) / 100 + margin, 2)
    return min(upper, just_above), table


async def sweep() -> dict[str, Any]:
    settings = get_settings()
    qs = [
        q for q in load_dataset(REPO / "eval" / "datasets" / "demo_golden.yaml") if q.split == "dev"
    ]
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    svc = RetrievalService(
        async_sessionmaker(engine, expire_on_commit=False),
        LocalEmbedder(settings),
        CrossEncoderReranker(settings),
        settings,
    )
    rows = []
    for q in qs:
        res = await svc.retrieve(dev_principal(q.as_user, settings), q.question)
        probs = [c.rerank_prob for c in res.chunks if c.rerank_prob is not None]
        rows.append(
            {
                "id": q.id,
                "category": q.category,
                "top_prob": probs[0] if probs else 0.0,
                "reranked": res.reranked,
            }
        )
    await engine.dispose()
    if not all(r["reranked"] for r in rows):
        raise RuntimeError("reranker unavailable: the sweep would be meaningless")
    chosen, table = choose(rows, settings.eval_threshold_margin)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset_sha256": file_sha256(REPO / "eval" / "datasets" / "demo_golden.yaml"),
        "split": "dev",
        "n_dev": len(rows),
        "rule": RULE,
        "margin": settings.eval_threshold_margin,
        "rerank_model": settings.rerank_model,
        "dev_top_probs": rows,
        "sweep": table,
        "chosen": chosen,
    }


def apply(chosen: float) -> None:
    text = CONFIG.read_text()
    new, n = re.subn(r"(abstain_rerank_threshold: float = )[0-9.]+", rf"\g<1>{chosen}", text)
    if n != 1:
        raise RuntimeError("could not find abstain_rerank_threshold in config.py")
    CONFIG.write_text(new)


def main() -> None:
    p = argparse.ArgumentParser(prog="eval.threshold")
    p.add_argument("--apply", action="store_true", help="write the chosen value into app/config.py")
    args = p.parse_args()
    result = asyncio.run(sweep())
    SWEEP.parent.mkdir(parents=True, exist_ok=True)
    SWEEP.write_text(json.dumps(result, indent=2) + "\n")
    print(f"dev questions: {result['n_dev']}  chosen threshold: {result['chosen']}  (rule: {RULE})")
    if args.apply:
        apply(result["chosen"])
        print("applied to backend/app/config.py")
    sys.exit(0)


if __name__ == "__main__":
    main()
