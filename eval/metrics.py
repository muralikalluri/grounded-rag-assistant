"""Pure metric functions for the eval harness. No I/O, no model calls."""

import math
import re
import unicodedata
from collections import Counter
from typing import Any

# Only these ``not_found`` reasons are genuine abstentions. The others mean the system failed to
# produce an answer (truncated / refused / unparsable output) and must never be credited.
ABSTAIN_STAGE1 = {"no_retrieval", "low_relevance"}
ABSTAIN_STAGE2 = {"model_not_found", "empty_answer", "ungrounded"}
SYSTEM_FAILURE = {"generation_truncated", "model_refused", "generation_invalid"}

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}  # fmt: skip
_WORDS_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b")


def norm(text: str) -> str:
    """NFKC, casefold, number words -> digits, drop thousands commas, collapse whitespace."""
    t = unicodedata.normalize("NFKC", text).casefold()
    t = _WORDS_RE.sub(lambda m: _NUMBER_WORDS[m.group(1)], t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    return " ".join(t.split())


def fact_present(answer: str, fact: str) -> bool:
    a, f = norm(answer), norm(fact)
    if not f:
        return False
    # Numbers must match as whole numbers ("12" must not match "120").
    pattern = (r"(?<![\d.])" if f[0].isdigit() else "") + re.escape(f)
    pattern += r"(?![\d])" if f[-1].isdigit() else ""
    return re.search(pattern, a) is not None


def fact_fraction(answer: str, facts: tuple[str, ...]) -> tuple[float, list[str]]:
    missing = [f for f in facts if not fact_present(answer, f)]
    return (len(facts) - len(missing)) / len(facts), missing


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion. (0, 1) when n == 0."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile. With n=30, p95 is effectively the 2nd-highest value."""
    if not values:
        return None
    s = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(s)))
    return s[rank - 1]


def classify(rec: dict[str, Any]) -> str:
    """error | answered | abstained_stage1 | abstained_stage2 | system_failure"""
    if rec.get("error"):
        return "error"
    resp = rec["response"]
    if resp["status"] == "answered":
        return "answered"
    reason = resp["grounding"]["abstained_reason"]
    if reason in ABSTAIN_STAGE1:
        return "abstained_stage1"
    if reason in ABSTAIN_STAGE2:
        return "abstained_stage2"
    if reason in SYSTEM_FAILURE:
        return "system_failure"
    return "system_failure"  # unknown reason: never credit as an abstention


def answer_text(resp: dict[str, Any]) -> str:
    return " ".join(s["text"] for s in resp.get("sentences", []))


def source_hit(chunk: dict[str, Any], expected: str) -> tuple[bool, bool]:
    """(file-level hit, page-level hit) for one retrieved chunk against 'file[#pN]'."""
    name, _, anchor = expected.partition("#")
    if chunk["source"] != name:
        return False, False
    if not anchor:
        return True, True
    page = int(anchor[1:])
    ps, pe = chunk.get("page_start"), chunk.get("page_end")
    return True, ps is not None and pe is not None and ps <= page <= pe


def recall(rec: dict[str, Any], expected: tuple[str, ...]) -> tuple[bool, bool | None]:
    """(file-level, page-level) recall for one question. Page-level is None if no page anchors."""
    file_hit = page_hit = False
    has_page = any("#p" in e for e in expected)
    for exp in expected:
        for ch in rec["retrieved"]:
            f, p = source_hit(ch, exp)
            file_hit |= f
            page_hit |= p and "#p" in exp
    return file_hit, (page_hit if has_page else None)


def rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n if n else None), "wilson95": [lo, hi]}


def aggregate(
    records: list[dict[str, Any]], questions: dict[str, Any], *, generation: bool
) -> dict[str, Any]:
    """Compute every metric from per-question records. Errors are excluded from all
    denominators and reported as ``n_errors``."""
    by_q = {r["id"]: r for r in records}
    out: dict[str, Any] = {"n_questions": len(records)}
    out["n_errors"] = sum(1 for r in records if r.get("error"))

    # ------------------------------------------------------------------ retrieval
    ans = [q for q in questions.values() if q.category == "answerable" and q.id in by_q]
    ok_ans = [q for q in ans if by_q[q.id].get("retrieved") is not None]
    file_hits = [recall(by_q[q.id], q.expected_sources) for q in ok_ans]
    out["recall_file"] = rate(sum(f for f, _ in file_hits), len(ok_ans))
    paged = [p for _, p in file_hits if p is not None]
    out["recall_page"] = rate(sum(bool(p) for p in paged), len(paged))
    out["acl_leaked_chunks"] = sum(r.get("acl_leaked_chunks", 0) for r in records)
    out["forbidden_source_hits"] = sum(
        1
        for q in questions.values()
        if q.id in by_q
        for ch in (by_q[q.id].get("retrieved") or [])
        if ch["source"] in q.forbidden_sources
    )
    out["chunks_checked_for_acl"] = sum(len(r.get("retrieved") or []) for r in records)
    out["rerank_unavailable_questions"] = sum(
        1 for r in records if (r.get("stage1") or {}).get("gate") == "skipped_no_rerank"
    )

    if not generation:
        return out

    # ----------------------------------------------------------------- generation
    outcomes = {qid: classify(r) for qid, r in by_q.items()}
    counts = Counter(outcomes.values())
    out["outcomes"] = dict(counts)
    valid = {qid for qid, o in outcomes.items() if o != "error"}

    answerable = [q for q in ans if q.id in valid]
    abstained_a = [q for q in answerable if outcomes[q.id].startswith("abstained")]
    out["false_abstention"] = rate(len(abstained_a), len(answerable))
    # Anything that is not an answer (abstention OR silent system failure) is a non-answer to the
    # user; report it next to the SPEC's "wrongly said not found" rate so failures cannot hide.
    not_answered = [q for q in answerable if outcomes[q.id] != "answered"]
    out["answerable_not_answered"] = rate(len(not_answered), len(answerable))
    out["false_abstention_stage1"] = sum(outcomes[q.id] == "abstained_stage1" for q in answerable)
    out["false_abstention_stage2"] = sum(outcomes[q.id] == "abstained_stage2" for q in answerable)
    out["answerable_system_failures"] = sum(outcomes[q.id] == "system_failure" for q in answerable)

    fracs: list[float] = []
    full = partial = none = 0
    stale = 0
    for q in answerable:
        if outcomes[q.id] != "answered":
            continue
        text = answer_text(by_q[q.id]["response"])
        frac = by_q[q.id].get("fact_fraction")
        if frac is None:
            frac, _ = fact_fraction(text, q.expected_facts)
        fracs.append(frac)
        full += frac == 1.0
        none += frac == 0.0
        partial += 0.0 < frac < 1.0
        stale += any(fact_present(text, f) for f in q.stale_facts)
    # End to end: right facts AND a citation from an expected source, over ALL answerable
    # questions (abstaining a lot cannot make this look good).
    e2e = 0
    for q in answerable:
        if outcomes[q.id] != "answered":
            continue
        rec_q = by_q[q.id]
        frac = rec_q.get("fact_fraction")
        if frac is None:
            frac, _ = fact_fraction(answer_text(rec_q["response"]), q.expected_facts)
        expected_files = {e.partition("#")[0] for e in q.expected_sources}
        by_chunk = {c["chunk_id"]: c["source"] for c in (rec_q.get("retrieved") or [])}
        cited = {by_chunk.get(c["chunk_id"]) for c in rec_q["response"].get("citations", [])}
        e2e += frac == 1.0 and bool(cited & expected_files)
    out["end_to_end_correct"] = rate(e2e, len(answerable))
    out["correctness"] = {
        "macro_fact_fraction": (sum(fracs) / len(fracs) if fracs else None),
        "n_answered": len(fracs),
        "full": full, "partial": partial, "none": none,
        "mentions_stale_fact": stale,
    }  # fmt: skip

    def abstention(cat: str) -> dict[str, Any]:
        qs = [q for q in questions.values() if q.category == cat and q.id in valid]
        good = 0
        leaks = 0
        for q in qs:
            text = answer_text(by_q[q.id]["response"]) if outcomes[q.id] == "answered" else ""
            leaked = any(fact_present(text, f) for f in q.denied_facts)
            leaks += leaked
            good += outcomes[q.id].startswith("abstained") and not leaked
        return {**rate(good, len(qs)), "denied_fact_leaks": leaks}

    ua, ad = abstention("unanswerable"), abstention("acl_denied")
    n_all = ua["n"] + ad["n"]
    out["abstention_unanswerable"] = ua
    out["abstention_acl_denied"] = ad
    out["abstention_combined"] = rate(ua["k"] + ad["k"], n_all)
    out["system_failures_on_negatives"] = sum(
        outcomes[q.id] == "system_failure"
        for q in questions.values()
        if q.category != "answerable" and q.id in valid
    )

    lat = [
        by_q[q.id]["latency_ms"]
        for q in questions.values()
        if q.id in valid and "latency_ms" in by_q[q.id]
    ]
    out["latency_ms"] = {"n": len(lat), "p50": percentile(lat, 50), "p95": percentile(lat, 95)}
    out["cost"] = cost_summary(records)
    return out


def cost_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum only priced calls; an unpriced call makes the total 'incomplete', never zero."""
    total = 0.0
    priced = unpriced = 0
    tokens_in = tokens_out = 0
    for r in records:
        for call in r.get("llm_calls", []):
            tokens_in += call.get("input_tokens") or 0
            tokens_out += call.get("output_tokens") or 0
            if call.get("cost_usd") is None:
                unpriced += 1
            else:
                priced += 1
                total += float(call["cost_usd"])
    return {
        "priced_calls": priced, "unpriced_calls": unpriced,
        "total_usd": (round(total, 6) if priced else None),
        "complete": unpriced == 0 and priced > 0,
        "input_tokens": tokens_in, "output_tokens": tokens_out,
    }  # fmt: skip
