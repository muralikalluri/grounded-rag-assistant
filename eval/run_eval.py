"""Run the golden set through the real pipeline and write results + a report.

    make eval-retrieval   # no LLM needed: retrieval recall, ACL leakage, stage-1 preview
    make eval             # full run; needs ANTHROPIC_API_KEY / OPENAI_API_KEY (costs money)

Provenance rules (enforced in code, tested): the offline `extractive` provider is a test double,
so its runs need --allow-test-mode, are labelled TEST MODE, and are written only under
results/eval/test-mode/, never to eval/reports/.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.principal import Principal, dev_principal
from app.config import Settings, get_settings
from app.generation.abstention import retrieval_gate
from app.generation.answer import ChatError, ChatService
from app.ingestion.embedding import LocalEmbedder
from app.llm.cost import PRICES_PER_MTOK
from app.llm.factory import build_provider
from app.llm.provider import LLMProvider
from app.llm.types import LLMResult
from app.retrieval.rerank import CrossEncoderReranker
from app.retrieval.service import RetrievalService
from app.retrieval.types import RetrievalResult
from eval import metrics as M
from eval import report as R
from eval.dataset import Question, file_sha256, load_dataset
from eval.judge import Judge, prompt_hash

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "eval" / "datasets" / "demo_golden.yaml"
CORPUS_MANIFEST = REPO / "demo-corpus" / "manifest.json"
REPORTS = REPO / "eval" / "reports"
APP_PROMPTS = ("answer", "user", "source_block")


# ----------------------------------------------------------------------- recording wrappers
class RecordingRetrieval:
    """Wraps RetrievalService and remembers the last result (chunks, for recall/ACL checks)."""

    def __init__(self, inner: RetrievalService) -> None:
        self._inner = inner
        self.last: RetrievalResult | None = None

    async def retrieve(
        self, principal: Principal, query: str, collection_ids: Any = None
    ) -> RetrievalResult:
        self.last = await self._inner.retrieve(principal, query, collection_ids)
        return self.last


class RecordingProvider:
    """Wraps an LLM provider and records every call's usage (tokens, cost, model)."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.generator = getattr(inner, "generator", "llm")
        self.results: list[LLMResult] = []

    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult:
        res = await self._inner.complete(system, user, json_schema)
        self.results.append(res)
        return res


def usage_of(results: list[LLMResult]) -> list[dict[str, Any]]:
    return [
        {
            "provider": a.provider, "model": a.model, "attempt": a.attempt, "outcome": a.outcome,
            "input_tokens": a.input_tokens, "output_tokens": a.output_tokens,
            "cost_usd": (str(a.cost_usd) if a.cost_usd is not None else None),
        }
        for r in results
        for a in r.attempts
    ]  # fmt: skip


# ------------------------------------------------------------------------------- provenance
def git_state() -> dict[str, Any]:
    def run(*a: str) -> str | None:
        try:
            r = subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=True)  # noqa: S603, S607
            return r.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    sha = run("rev-parse", "--short", "HEAD")
    status = run("status", "--porcelain")
    return {"sha": sha or "no-commits-yet", "dirty": bool(status) or sha is None}


def env_fingerprint() -> dict[str, Any]:
    """Library versions and compute device: the same code can score differently across them."""
    import importlib.metadata as md

    import torch

    def ver(pkg: str) -> str | None:
        try:
            return md.version(pkg)
        except md.PackageNotFoundError:
            return None

    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    pkgs = ("torch", "sentence-transformers", "transformers", "pymupdf", "litellm")
    return {"python": sys.version.split()[0], **{p: ver(p) for p in pkgs}, "device": device}


async def db_fingerprint(engine: Any) -> dict[str, Any]:
    """What is actually ingested (not just what is in the corpus folder)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks), "
                    "md5(coalesce((SELECT string_agg(checksum_sha256, ',' "
                    "ORDER BY checksum_sha256) FROM documents), ''))"
                )
            )
        ).one()
    return {"documents": row[0], "chunks": row[1], "checksums_md5": row[2]}


def build_manifest(
    settings: Settings,
    mode: str,
    run_id: str,
    split: str,
    models_seen: list[str],
    judge_desc: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompts = {
        name: hashlib.sha256(
            (REPO / "backend" / "app" / "generation" / "prompts" / f"{name}.md").read_bytes()
        ).hexdigest()
        for name in APP_PROMPTS
    }
    prompts.update({n: prompt_hash(n) for n in ("judge_fact", "judge_citation")})
    return {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "split": split,
        "git": git_state(),
        "dataset_sha256": file_sha256(DATASET),
        "corpus_manifest_sha256": file_sha256(CORPUS_MANIFEST),
        "prompt_sha256": prompts,
        "models_seen": models_seen,
        "judge": judge_desc,
        "config": {  # whitelisted, non-secret
            "embedding_model": settings.embedding_model,
            "rerank_model": settings.rerank_model,
            "abstain_rerank_threshold": settings.abstain_rerank_threshold,
            "retrieval_vector_k": settings.retrieval_vector_k,
            "retrieval_keyword_k": settings.retrieval_keyword_k,
            "rrf_k": settings.rrf_k,
            "rerank_candidates": settings.rerank_candidates,
            "rerank_top_n": settings.rerank_top_n,
            # No generator ran in retrieval-only mode: do not pretend one did.
            "llm_provider": None if mode == "retrieval_only" else settings.llm_provider,
            "llm_model": (
                None
                if mode == "retrieval_only"
                else {"anthropic": settings.anthropic_model, "openai": settings.openai_model}.get(
                    settings.llm_provider
                )
            ),
            "temperature": (
                None
                if mode == "retrieval_only"
                else (
                    "not sent" if settings.llm_provider == "anthropic" else settings.llm_temperature
                )
            ),
            "quote_fuzzy_token_tolerance": settings.quote_fuzzy_token_tolerance,
            "chunk_max_tokens": settings.chunk_max_tokens,
            "chunk_overlap_ratio": settings.chunk_overlap_ratio,
            "eval_concurrency": settings.eval_concurrency,
            "eval_retries_per_question": settings.eval_retries_per_question,
        },
        **(extra or {}),
    }


def estimate_cost(settings: Settings, n_questions: int) -> tuple[Decimal | None, str]:
    """Rough pre-run estimate for the generator only (judge calls add more)."""
    model = (
        settings.anthropic_model if settings.llm_provider == "anthropic" else settings.openai_model
    )
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None, model
    per = (
        Decimal(settings.eval_est_input_tokens) * price[0]
        + Decimal(settings.eval_est_output_tokens) * price[1]
    )
    return per * n_questions / Decimal(1_000_000), model


# ------------------------------------------------------------------------------ per question
async def acl_leaks(engine: Any, principal: Principal, chunk_ids: list[str]) -> int:
    """Independent audit: how many returned chunks does this user's ACL NOT allow?"""
    if not chunk_ids:
        return 0
    async with engine.connect() as conn:
        rows = await conn.execute(
            sa.text(
                "SELECT count(*) FROM chunks WHERE id = ANY(CAST(:ids AS uuid[])) "
                "AND NOT (acl_groups && CAST(:groups AS text[]))"
            ),
            {"ids": chunk_ids, "groups": sorted(principal.groups)},
        )
        return int(rows.scalar() or 0)


def chunk_view(c: Any, source_prefix: str = "upload://") -> dict[str, Any]:
    return {
        "chunk_id": str(c.chunk_id),
        "source": c.source_uri.removeprefix(source_prefix),
        "page_start": c.page_start,
        "page_end": c.page_end,
        "rerank_prob": c.rerank_prob,
    }


async def run_question(
    q: Question, settings: Settings, retrieval: RetrievalService, engine: Any,
    provider: LLMProvider | None, retries: int,
) -> dict[str, Any]:  # fmt: skip
    principal = dev_principal(q.as_user, settings)
    rec: dict[str, Any] = {
        "id": q.id, "as_user": q.as_user, "category": q.category, "split": q.split,
    }  # fmt: skip
    t0 = asyncio.get_running_loop().time()
    for _ in range(retries + 1):
        recording = RecordingRetrieval(retrieval)
        try:
            if provider is None:  # retrieval only
                await recording.retrieve(principal, q.question)
                rec["response"] = None
            else:
                rec_provider = RecordingProvider(provider)
                service = ChatService(recording, rec_provider, settings)  # type: ignore[arg-type]

                async def noop(event: str, data: dict[str, Any]) -> None:
                    return None

                resp = await service.run(principal, q.question, None, noop)
                rec["response"] = resp.model_dump(mode="json")
                rec["generator"] = resp.generator
                rec["llm_calls"] = usage_of(rec_provider.results)
            rec["error"] = None
            break
        except ChatError as exc:
            rec["error"] = exc.code
            rec["response"] = None
        except Exception as exc:
            rec["error"] = f"unexpected:{type(exc).__name__}"  # type only: never log content
            rec["response"] = None
    rec["latency_ms"] = (asyncio.get_running_loop().time() - t0) * 1000
    last = recording.last
    if rec["error"] is None or last is not None:
        chunks = last.chunks if last else []
        rec["retrieved"] = [chunk_view(c) for c in chunks]
        rec["acl_leaked_chunks"] = await acl_leaks(
            engine, principal, [str(c.chunk_id) for c in chunks]
        )
        gate = retrieval_gate(last, settings) if last else None
        rec["stage1"] = {"top_prob": gate.top_prob, "gate": gate.gate} if gate else None
    else:
        rec["retrieved"] = None
    return rec


def read_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = r
    return out


# ------------------------------------------------------------------------------------ judging
async def apply_judge(
    records: list[dict[str, Any]],
    qs: dict[str, Question],
    judge: Judge,
    budget_usd: float | None = None,
) -> dict[str, Any]:
    """Rescue string-match misses and grade citation entailment. Returns extra metrics."""
    rescues = 0
    cit_ok = cit_n = 0
    judge_cost = 0.0
    out_of_budget = False
    for r in records:
        if r.get("error") or not r.get("response") or r["response"]["status"] != "answered":
            continue
        if budget_usd is not None and judge_cost > budget_usd:
            out_of_budget = True  # judge spend counts toward the eval cost cap
            break
        q = qs[r["id"]]
        text = M.answer_text(r["response"])
        if q.category == "answerable":
            frac, missing = M.fact_fraction(text, q.expected_facts)
            for fact in missing:
                v = await judge.fact_supported(text, fact)
                judge_cost += v.cost_usd or 0.0
                if v.supported:
                    rescues += 1
                    frac += 1 / len(q.expected_facts)
            r["fact_fraction"] = min(frac, 1.0)
        cites = {c["n"]: c for c in r["response"]["citations"]}
        for s in r["response"]["sentences"]:
            for n in s["citations"]:
                v = await judge.citation_supported(s["text"], cites[n]["quote"])
                judge_cost += v.cost_usd or 0.0
                cit_n += 1
                cit_ok += v.supported
    out: dict[str, Any] = {
        "judge_rescues": rescues,
        "judge_cost_usd": round(judge_cost, 6),
        "judge_stopped_at_cost_cap": out_of_budget,
    }
    out["citation_precision"] = M.rate(cit_ok, cit_n)
    return out


# --------------------------------------------------------------------------------------- main
async def amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    all_q = load_dataset(DATASET)
    questions = [q for q in all_q if args.split == "all" or q.split == args.split]
    if args.limit:
        questions = questions[: args.limit]
    qmap = {q.id: q for q in questions}

    if args.retrieval_only:
        mode = "retrieval_only"
    elif settings.llm_provider == "extractive":
        if not args.allow_test_mode:
            print("REFUSED: llm_provider=extractive is a test double, not a model. Its results are "
                  "not model quality. Use --allow-test-mode to run it (output goes to "
                  "results/eval/test-mode/ only).", file=sys.stderr)  # fmt: skip
            return 2
        mode = "test_mode"
    else:
        mode = "llm"

    if mode == "llm":
        est, model = estimate_cost(settings, len(questions))
        print(
            f"Generator: {settings.llm_provider}/{model}; {len(questions)} questions; "
            f"hard cap ${settings.eval_max_cost_usd}"
        )
        if est is None and not args.yes_unpriced:
            print(
                f"REFUSED: no price on file for {model}, so the cost cannot be estimated. "
                "Re-run with --yes-unpriced.",
                file=sys.stderr,
            )
            return 3
        if est is not None:
            print(f"Estimated generator cost: ~${est:.2f} (judge calls extra)")
        if not args.yes:
            print("Dry run only. Re-run with --yes to spend money.", file=sys.stderr)
            return 3

    run_id = args.resume or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    root = REPO / "results" / "eval" / ("test-mode" if mode == "test_mode" else "runs")
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "records.jsonl"
    meta_path = run_dir / "meta.json"
    meta = {"mode": mode, "dataset_sha256": file_sha256(DATASET), "split": args.split}
    if meta_path.exists():
        if json.loads(meta_path.read_text()) != meta:
            print(
                "REFUSED: --resume run was started with a different mode, dataset or split.",
                file=sys.stderr,
            )
            return 5
    else:
        meta_path.write_text(json.dumps(meta))
    done = {qid: r for qid, r in read_records(jsonl).items() if not r.get("error")}

    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    retrieval = RetrievalService(
        sf, LocalEmbedder(settings), CrossEncoderReranker(settings), settings
    )
    await retrieval.check_pgvector_version()
    provider = None if mode == "retrieval_only" else build_provider(settings)

    sem = asyncio.Semaphore(settings.eval_concurrency)
    spent = 0.0
    incomplete_reason: str | None = None
    lock = asyncio.Lock()

    async def one(q: Question) -> None:
        nonlocal spent, incomplete_reason
        if q.id in done or incomplete_reason:
            return
        async with sem:
            if incomplete_reason:
                return
            rec = await run_question(
                q, settings, retrieval, engine, provider, settings.eval_retries_per_question
            )
        async with lock:
            spent += sum(
                float(c["cost_usd"]) for c in rec.get("llm_calls", []) if c.get("cost_usd")
            )
            with jsonl.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            done[q.id] = rec
            if mode == "llm" and spent > settings.eval_max_cost_usd:
                incomplete_reason = (
                    f"cost cap ${settings.eval_max_cost_usd} reached (spent ${spent:.2f})"
                )

    await asyncio.gather(*[one(q) for q in questions])
    records = [done[q.id] for q in questions if q.id in done]
    if len(records) < len(questions) and not incomplete_reason:
        incomplete_reason = f"{len(questions) - len(records)} question(s) have no record"
    n_err = sum(1 for r in records if r.get("error"))
    if n_err and not incomplete_reason:
        incomplete_reason = f"{n_err} question(s) ended in errors after retries"

    # ---- judge (real providers only; never for the test double) -----------------------------
    extra: dict[str, Any] = {}
    judge_desc = "disabled"
    if mode == "llm" and settings.eval_judge_provider:
        jset = settings.model_copy(update={"llm_provider": settings.eval_judge_provider})
        if settings.eval_judge_model:
            jset = jset.model_copy(
                update={
                    "anthropic_model": settings.eval_judge_model,
                    "openai_model": settings.eval_judge_model,
                }
            )
        same = settings.eval_judge_provider == settings.llm_provider
        judge_desc = f"{settings.eval_judge_provider}/{settings.eval_judge_model or 'default'}" + (
            " (same family as generator: unverified)" if same else ""
        )
        remaining = max(0.0, settings.eval_max_cost_usd - spent)
        extra = await apply_judge(records, qmap, Judge(build_provider(jset), settings), remaining)
        if extra.get("judge_stopped_at_cost_cap") and not incomplete_reason:
            incomplete_reason = "judge stopped at the cost cap: citation precision is partial"

    metrics = M.aggregate(records, qmap, generation=mode != "retrieval_only")
    metrics.update(extra)
    metrics["stage1_preview"] = stage1_preview(records, qmap, settings)
    models_seen = sorted({c["model"] for r in records for c in r.get("llm_calls", [])})
    manifest = build_manifest(
        settings,
        mode,
        run_id,
        args.split,
        models_seen,
        judge_desc,
        {"environment": env_fingerprint(), "database": await db_fingerprint(engine)},
    )
    result = {
        "manifest": manifest,
        "metrics": metrics,
        "incomplete": bool(incomplete_reason),
        "incomplete_reason": incomplete_reason,
        "per_question": [per_question_row(r, qmap[r["id"]]) for r in records],
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    await engine.dispose()

    reports_dir = REPORTS if mode != "test_mode" else run_dir
    previous = R.find_previous(REPORTS, result) if mode != "test_mode" else None
    md = R.render_markdown(result, previous)
    (run_dir / "report.md").write_text(md)
    print(md.split("## Per question")[0])

    if mode != "test_mode":
        try:
            R.assert_promotable(
                result, allow_dirty=args.allow_dirty, allow_partial=args.allow_partial
            )
        except R.PromotionRefused as exc:
            print(f"Not promoted to eval/reports: {exc}. Results are in {run_dir}", file=sys.stderr)
            return 4
        suffix = "_retrieval-only" if mode == "retrieval_only" else ""
        name = f"{datetime.now(UTC):%Y-%m-%d}_{manifest['git']['sha']}{suffix}"
        REPORTS.mkdir(parents=True, exist_ok=True)
        n = 2
        while (REPORTS / f"{name}.json").exists():  # never overwrite a committed report
            name = f"{datetime.now(UTC):%Y-%m-%d}_{manifest['git']['sha']}{suffix}_{n}"
            n += 1
        (REPORTS / f"{name}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        (REPORTS / f"{name}.md").write_text(md)
        print(f"Report written: eval/reports/{name}.md")
    else:
        print(f"TEST MODE: results only in {reports_dir}")
    return 0


def stage1_preview(
    records: list[dict[str, Any]], qs: dict[str, Question], settings: Settings
) -> dict[str, Any]:
    """Would stage 1 abstain? Reported per split: dev is the tuning set (in-sample)."""
    out: dict[str, Any] = {}
    for split in ("holdout", "dev"):
        out[split] = {}
        for cat in ("answerable", "unanswerable", "acl_denied"):
            rows = [
                r
                for r in records
                if r["category"] == cat and r.get("split") == split and r.get("stage1") is not None
            ]
            # no chunks at all (top_prob None) abstains at stage 1 too
            k = sum(
                r["stage1"]["top_prob"] is None
                or r["stage1"]["top_prob"] < settings.abstain_rerank_threshold
                for r in rows
            )
            out[split][cat] = {"k": k, "n": len(rows)}
    return out


def per_question_row(r: dict[str, Any], q: Question) -> dict[str, Any]:
    outcome = M.classify(r) if r.get("response") is not None or r.get("error") else "retrieval_only"
    notes: list[str] = []
    if r.get("acl_leaked_chunks"):
        notes.append(f"ACL LEAK x{r['acl_leaked_chunks']}")
    if r.get("error"):
        notes.append(f"error={r['error']}")
    if r.get("response") and r["response"]["grounding"]["abstained_reason"]:
        notes.append(r["response"]["grounding"]["abstained_reason"])
    file_hit = "n/a"
    if q.category == "answerable" and r.get("retrieved") is not None:
        f, p = M.recall(r, q.expected_sources)
        file_hit = ("hit" if f else "MISS") + (
            "" if p is None else (" / page hit" if p else " / page MISS")
        )
    return {
        "id": q.id,
        "as_user": q.as_user,
        "category": q.category,
        "outcome": outcome,
        "recall_file": file_hit,
        "notes": "; ".join(notes),
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="eval.run_eval")
    p.add_argument(
        "--retrieval-only", action="store_true", help="no LLM: retrieval + ACL + stage-1 preview"
    )
    p.add_argument(
        "--allow-test-mode",
        action="store_true",
        help="allow the extractive test double (output stays out of eval/reports)",
    )
    p.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--resume", metavar="RUN_ID")
    p.add_argument("--yes", action="store_true", help="spend money on a real provider")
    p.add_argument(
        "--yes-unpriced", action="store_true", help="run even though the model has no price on file"
    )
    p.add_argument("--allow-dirty", action="store_true")
    p.add_argument("--allow-partial", action="store_true")
    sys.exit(asyncio.run(amain(p.parse_args())))


if __name__ == "__main__":
    main()
