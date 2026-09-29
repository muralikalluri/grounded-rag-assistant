# ruff: noqa: E501  (dense test data tables)
import math
from typing import Any

import pytest
from eval.dataset import Question
from eval.metrics import (
    aggregate,
    classify,
    cost_summary,
    fact_fraction,
    fact_present,
    norm,
    percentile,
    recall,
    wilson,
)


def test_fact_matching_is_whole_number_and_form_insensitive() -> None:
    assert fact_present("You may carry forward up to 10 days.", "10 days")
    assert fact_present("You may carry forward up to ten days.", "10 days")
    assert not fact_present("You get 120 days.", "12")
    assert not fact_present("Version 1.12 applies.", "12")
    assert fact_present("The band is 80,000 to 102,000 euros.", "102,000")
    assert fact_present("The band is 80000 to 102000 euros.", "102,000")
    assert fact_present("Deployments are frozen on Fridays.", "Friday")
    assert not fact_present("", "x") and not fact_present("text", "")
    assert norm("ＴＥＮ  Days") == "10 days"


def test_fact_fraction_reports_missing_facts() -> None:
    assert fact_fraction("80,000 to 102,000", ("80,000", "102,000")) == (1.0, [])
    frac, missing = fact_fraction("only 80,000 here", ("80,000", "102,000"))
    assert frac == 0.5 and missing == ["102,000"]


def test_wilson_interval_values_and_small_sample_honesty() -> None:
    lo, hi = wilson(4, 4)
    assert 0.49 < lo < 0.52 and hi == 1.0  # 4/4 cannot demonstrate a >= 0.95 target
    lo, hi = wilson(19, 20)
    assert 0.76 < lo < 0.77 and 0.98 < hi < 1.0
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(0, 10)[0] == 0.0 and math.isclose(wilson(10, 10)[1], 1.0)


def test_percentile_nearest_rank() -> None:
    vals = [float(i) for i in range(1, 31)]
    assert percentile(vals, 50) == 15.0 and percentile(vals, 95) == 29.0
    assert percentile([], 50) is None and percentile([7.0], 95) == 7.0


def resp(status: str, reason: str | None = None, text: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "sentences": [{"text": text, "citations": []}] if text else [],
        "citations": [],
        "grounding": {"abstained_reason": reason, "stage1": {"gate": "passed"}},
    }


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_retrieval", "abstained_stage1"), ("low_relevance", "abstained_stage1"),
        ("model_not_found", "abstained_stage2"), ("empty_answer", "abstained_stage2"),
        ("ungrounded", "abstained_stage2"),
        ("generation_truncated", "system_failure"), ("model_refused", "system_failure"),
        ("generation_invalid", "system_failure"), ("something_new", "system_failure"),
    ],
)  # fmt: skip
def test_only_real_abstentions_are_credited(reason: str, expected: str) -> None:
    assert classify({"response": resp("not_found", reason)}) == expected


def test_errors_and_answers_classify() -> None:
    assert classify({"error": "llm_unavailable", "response": None}) == "error"
    assert classify({"response": resp("answered", None, "x")}) == "answered"


def q(qid: str, cat: str, **kw: Any) -> Question:
    return Question(id=qid, question="?", category=cat, as_user="alice", split="dev", **kw)


def rec(qid: str, cat: str, response: dict[str, Any] | None, *, retrieved: list[dict[str, Any]] | None = None,
        error: str | None = None, leaked: int = 0, calls: list[dict[str, Any]] | None = None,
        latency: float = 100.0) -> dict[str, Any]:  # fmt: skip
    return {"id": qid, "category": cat, "response": response, "error": error, "retrieved": retrieved or [],
            "acl_leaked_chunks": leaked, "llm_calls": calls or [], "latency_ms": latency}  # fmt: skip


def chunk(source: str, ps: int | None = None, pe: int | None = None) -> dict[str, Any]:
    return {"chunk_id": "c", "source": source, "page_start": ps, "page_end": pe, "rerank_prob": 0.9}


def test_recall_file_and_page_level() -> None:
    r = rec("a", "answerable", None, retrieved=[chunk("x.pdf", 3, 4), chunk("y.docx")])
    assert recall(r, ("x.pdf#p4",)) == (True, True)
    assert recall(r, ("x.pdf#p5",)) == (True, False)  # right file, wrong page
    assert recall(r, ("y.docx",)) == (True, None)  # no page anchor -> no page-level result
    assert recall(r, ("z.pdf#p1",)) == (False, False)


def test_aggregate_scores_abstention_recall_leaks_and_excludes_errors() -> None:
    qs = {
        "a1": q("a1", "answerable", expected_facts=("10 days",), expected_sources=("f.pdf#p4",), stale_facts=("5 days",)),
        "a2": q("a2", "answerable", expected_facts=("24 days",), expected_sources=("g.pdf",)),
        "a3": q("a3", "answerable", expected_facts=("x",), expected_sources=("h.pdf",)),
        "a4": q("a4", "answerable", expected_facts=("y",), expected_sources=("h.pdf",)),
        "u1": q("u1", "unanswerable", absent_terms=("t",)),
        "u2": q("u2", "unanswerable", absent_terms=("t",)),
        "d1": q("d1", "acl_denied", forbidden_sources=("hr.pdf",), denied_facts=("102,000",)),
        "d2": q("d2", "acl_denied", forbidden_sources=("hr.pdf",), denied_facts=("60-day",)),
    }  # fmt: skip
    records = [
        rec("a1", "answerable", resp("answered", None, "Up to 10 days (was 5 days)."), retrieved=[chunk("f.pdf", 4, 4)]),
        rec("a2", "answerable", resp("not_found", "low_relevance"), retrieved=[chunk("other.pdf")]),
        rec("a3", "answerable", resp("not_found", "generation_truncated"), retrieved=[chunk("h.pdf")]),
        rec("a4", "answerable", None, error="llm_unavailable", retrieved=[chunk("h.pdf")]),
        rec("u1", "unanswerable", resp("not_found", "model_not_found")),
        rec("u2", "unanswerable", resp("not_found", "model_refused")),
        rec("d1", "acl_denied", resp("not_found", "ungrounded"), leaked=0),
        rec("d2", "acl_denied", resp("answered", None, "A 60-day plan applies."), leaked=2),
    ]  # fmt: skip
    m = aggregate(records, qs, generation=True)
    assert m["n_errors"] == 1
    # errors leave every denominator: 3 answerable counted, 1 false abstention (a2); a3 is a
    # system failure (neither abstention nor answer)
    assert m["false_abstention"]["n"] == 3 and m["false_abstention"]["k"] == 1
    assert m["false_abstention_stage1"] == 1 and m["answerable_system_failures"] == 1
    # truncated/refused outputs are NOT credited as abstentions
    assert m["abstention_unanswerable"]["k"] == 1 and m["abstention_unanswerable"]["n"] == 2
    assert m["system_failures_on_negatives"] == 1
    # an answer containing a denied fact fails the ACL-denied abstention and is counted as a leak
    assert (
        m["abstention_acl_denied"]["k"] == 1
        and m["abstention_acl_denied"]["denied_fact_leaks"] == 1
    )
    assert m["abstention_combined"]["k"] == 2 and m["abstention_combined"]["n"] == 4
    assert m["correctness"]["full"] == 1 and m["correctness"]["mentions_stale_fact"] == 1
    assert m["acl_leaked_chunks"] == 2
    # retrieval is scored even when generation errored: a1, a3, a4 retrieved their source; a2 did not
    assert m["recall_file"]["n"] == 4 and m["recall_file"]["k"] == 3
    assert m["recall_page"]["n"] == 1 and m["recall_page"]["k"] == 1  # only a1 has a page anchor


def test_retrieval_only_aggregation_needs_no_responses() -> None:
    qs = {"a1": q("a1", "answerable", expected_facts=("x",), expected_sources=("f.pdf",))}
    m = aggregate([rec("a1", "answerable", None, retrieved=[chunk("f.pdf")])], qs, generation=False)
    assert m["recall_file"]["k"] == 1 and "outcomes" not in m and m["acl_leaked_chunks"] == 0


def test_forbidden_source_hits_are_counted_from_retrieval() -> None:
    qs = {"d1": q("d1", "acl_denied", forbidden_sources=("hr.pdf",), denied_facts=("x",))}
    r = rec("d1", "acl_denied", None, retrieved=[chunk("hr.pdf"), chunk("ok.pdf")])
    assert aggregate([r], qs, generation=False)["forbidden_source_hits"] == 1


def test_unpriced_calls_make_cost_incomplete_never_zero() -> None:
    priced = {"cost_usd": "0.010", "input_tokens": 100, "output_tokens": 10}
    unpriced = {"cost_usd": None, "input_tokens": 50, "output_tokens": 5}
    s = cost_summary([{"llm_calls": [priced, unpriced]}])
    assert s["total_usd"] == 0.01 and s["complete"] is False and s["unpriced_calls"] == 1
    none = cost_summary([{"llm_calls": [unpriced]}])
    assert none["total_usd"] is None and none["complete"] is False  # None, not $0.00
    assert cost_summary([{"llm_calls": [priced]}])["complete"] is True


def test_a_retrieval_failure_record_does_not_crash_aggregation() -> None:
    """Regression: run_question stores retrieved=None when retrieval itself fails."""
    qs = {
        "a1": q("a1", "answerable", expected_facts=("x",), expected_sources=("f.pdf",)),
        "d1": q("d1", "acl_denied", forbidden_sources=("hr.pdf",), denied_facts=("y",)),
    }
    records = [
        {"id": "a1", "category": "answerable", "response": None, "error": "retrieval_failed",
         "retrieved": None, "latency_ms": 5.0},
        {"id": "d1", "category": "acl_denied", "response": None, "error": "retrieval_failed",
         "retrieved": None, "latency_ms": 5.0},
    ]  # fmt: skip
    for generation in (False, True):
        m = aggregate(records, qs, generation=generation)
        assert m["n_errors"] == 2 and m["chunks_checked_for_acl"] == 0
        assert m["recall_file"]["n"] == 0 and m["forbidden_source_hits"] == 0


def _answered(qid: str, text: str, cited_chunk: str) -> dict[str, Any]:
    r = resp("answered", None, text)
    r["citations"] = [{"chunk_id": cited_chunk}]
    return rec(qid, "answerable", r, retrieved=[{**chunk("right.pdf"), "chunk_id": "c-right"},
                                               {**chunk("wrong.pdf"), "chunk_id": "c-wrong"}])  # fmt: skip


def test_end_to_end_correctness_needs_the_right_facts_and_the_right_source() -> None:
    qs = {
        i: q(i, "answerable", expected_facts=("10 days",), expected_sources=("right.pdf",))
        for i in "abcd"
    }
    records = [
        _answered("a", "Up to 10 days.", "c-right"),  # right fact, right source -> correct
        _answered("b", "Up to 10 days.", "c-wrong"),  # right fact from the WRONG document
        _answered("c", "Up to 5 days.", "c-right"),  # wrong fact, right source
        rec("d", "answerable", resp("not_found", "low_relevance"), retrieved=[chunk("right.pdf")]),
    ]
    m = aggregate(records, qs, generation=True)
    assert m["end_to_end_correct"]["k"] == 1 and m["end_to_end_correct"]["n"] == 4
    # abstaining a lot cannot flatter the figure: the denominator is ALL answerable questions
    assert m["correctness"]["full"] == 2  # fact-only correctness is higher, by design visible


def test_answerable_not_answered_includes_silent_system_failures() -> None:
    qs = {i: q(i, "answerable", expected_facts=("x",), expected_sources=("f.pdf",)) for i in "abc"}
    records = [
        rec("a", "answerable", resp("not_found", "model_not_found"), retrieved=[chunk("f.pdf")]),
        rec(
            "b", "answerable", resp("not_found", "generation_truncated"), retrieved=[chunk("f.pdf")]
        ),
        rec("c", "answerable", resp("answered", None, "x"), retrieved=[chunk("f.pdf")]),
    ]
    m = aggregate(records, qs, generation=True)
    assert m["false_abstention"]["k"] == 1  # the SPEC definition: wrongly said not found
    assert m["answerable_not_answered"]["k"] == 2  # ... plus the truncated one that also failed
