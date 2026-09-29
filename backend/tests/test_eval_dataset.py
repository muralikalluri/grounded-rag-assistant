"""The golden set must be verifiable against the corpus: no unanswerable question whose answer
exists, no ACL-denied question the user can actually read, no expected fact that is not there."""

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pymupdf
import pytest
from eval.dataset import DatasetError, load_dataset, parse_source
from eval.metrics import fact_present, norm

from app.config import Settings
from app.ingestion.connectors.upload import resolve_mime
from app.ingestion.parsers import parse_document
from tests.conftest import REPO_ROOT

CORPUS = REPO_ROOT / "demo-corpus"
GROUPS = {"alice": {"HR"}, "bob": {"Engineering"}}
QUESTIONS = load_dataset(REPO_ROOT / "eval" / "datasets" / "demo_golden.yaml")


@lru_cache
def corpus() -> dict[str, dict[str, object]]:
    """file name -> {text, pages: [text per page] | None, acl: set[str]}"""
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    acl = {c["name"]: set(c["acl_groups"]) for c in manifest["collections"]}
    out: dict[str, dict[str, object]] = {}
    for d in manifest["documents"]:
        path = CORPUS / d["path"]
        data = path.read_bytes()
        doc = parse_document(resolve_mime(path.name, data), data, path.stem, Settings())
        pages = None
        if path.suffix == ".pdf":
            with pymupdf.open(path) as pdf:
                pages = [" ".join(p.get_text().split()) for p in pdf]
        out[path.name] = {
            "text": " ".join(b.text for b in doc.blocks),
            "pages": pages,
            "acl": acl[d["collection"]],
        }
    return out


def readable(user: str, name: str) -> bool:
    return bool(corpus()[name]["acl"] & GROUPS[user])  # type: ignore[operator]


def test_shape_matches_the_mvp_cut() -> None:
    assert len(QUESTIONS) == 30
    assert Counter(q.category for q in QUESTIONS) == {
        "answerable": 20,
        "unanswerable": 6,
        "acl_denied": 4,
    }
    assert len({q.id for q in QUESTIONS}) == 30
    by = Counter((q.category, q.split) for q in QUESTIONS)
    assert all(
        by[(c, s)] >= 2
        for c in ("answerable", "unanswerable", "acl_denied")
        for s in ("dev", "holdout")
    )


def test_every_expected_fact_occurs_in_its_expected_source_and_page() -> None:
    for q in (x for x in QUESTIONS if x.category == "answerable"):
        for spec in q.expected_sources:
            name, page = parse_source(spec)
            doc = corpus()[name]
            haystack = doc["text"] if page is None else doc["pages"][page - 1]  # type: ignore[index]
            for fact in q.expected_facts:
                assert fact_present(str(haystack), fact), f"{q.id}: {fact!r} not in {spec}"


def test_expected_facts_identify_the_right_document_not_just_a_common_number() -> None:
    """A wrong-source answer must not be able to score as correct: no document other than the
    expected sources may contain ALL of a question's expected facts."""
    for q in (x for x in QUESTIONS if x.category == "answerable"):
        expected = {parse_source(s)[0] for s in q.expected_sources}
        others = [
            name
            for name, d in corpus().items()
            if name not in expected
            and all(fact_present(str(d["text"]), f) for f in q.expected_facts)
        ]
        assert not others, f"{q.id}: facts {q.expected_facts} also appear in {others[:4]}"


def test_answerable_questions_are_answerable_by_the_asking_user() -> None:
    for q in (x for x in QUESTIONS if x.category == "answerable"):
        assert any(readable(q.as_user, parse_source(s)[0]) for s in q.expected_sources), q.id


def test_unanswerable_questions_really_have_no_answer_in_the_corpus() -> None:
    for q in (x for x in QUESTIONS if x.category == "unanswerable"):
        for term in q.absent_terms:
            hits = [n for n, d in corpus().items() if term.lower() in norm(str(d["text"]))]
            assert hits == [], f"{q.id}: {term!r} occurs in {hits}"


def test_acl_denied_facts_exist_only_where_the_user_cannot_read() -> None:
    for q in (x for x in QUESTIONS if x.category == "acl_denied"):
        for fact in q.denied_facts:
            holders = [n for n, d in corpus().items() if fact_present(str(d["text"]), fact)]
            assert holders, f"{q.id}: denied fact {fact!r} occurs nowhere"
            assert not [n for n in holders if readable(q.as_user, n)], (
                f"{q.id}: {q.as_user} can read {holders}"
            )
        for src in q.forbidden_sources:
            assert not readable(q.as_user, src), f"{q.id}: {src} is readable by {q.as_user}"


def test_stale_facts_belong_to_the_superseded_document_not_the_current_one() -> None:
    for q in (x for x in QUESTIONS if x.stale_facts):
        current = corpus()[parse_source(q.expected_sources[0])[0]]["text"]
        older = corpus()[parse_source(q.expected_sources[0])[0].replace("v2", "v1")]["text"]
        for fact in q.stale_facts:
            assert fact_present(str(older), fact) and not fact_present(str(current), fact), q.id


def test_no_gold_fact_leaks_into_a_prompt_template() -> None:
    prompts = "\n".join(
        p.read_text() for p in (REPO_ROOT / "backend/app/generation/prompts").glob("*.md")
    )
    prompts_n = norm(prompts)
    for q in QUESTIONS:
        for fact in (*q.expected_facts, *q.denied_facts):
            if len(fact) > 3:  # trivial numbers like "12" legitimately appear in prose
                assert norm(fact) not in prompts_n, f"{q.id}: {fact!r} appears in a prompt"


def test_dataset_is_discriminating_across_formats_and_distractors() -> None:
    suffixes = {Path(parse_source(s)[0]).suffix for q in QUESTIONS for s in q.expected_sources}
    assert {".pdf", ".docx", ".html", ".md"} <= suffixes
    tags = Counter(t for q in QUESTIONS for t in q.tags)
    assert tags["versioned"] >= 2 and tags["near-duplicate"] >= 4 and tags["paraphrase"] >= 2


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"category": "nope"}, "bad category"),
        ({"as_user": "carol"}, "bad as_user"),
        ({"split": "test"}, "bad split"),
        ({"expected_facts": []}, "needs expected_facts"),
    ],
)
def test_loader_rejects_malformed_questions(
    tmp_path: Path, patch: dict[str, object], message: str
) -> None:
    import yaml

    base = {
        "id": "q1", "question": "x?", "category": "answerable", "as_user": "alice",
        "split": "dev", "expected_facts": ["a"], "expected_sources": ["f.pdf"],
    }  # fmt: skip
    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump([{**base, **patch}]))
    with pytest.raises(DatasetError, match=message):
        load_dataset(p)
