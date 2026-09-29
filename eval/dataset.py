"""Golden dataset loading and validation."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATEGORIES = ("answerable", "unanswerable", "acl_denied")
SPLITS = ("dev", "holdout")
USERS = ("alice", "bob")


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    category: str
    as_user: str
    split: str
    expected_facts: tuple[str, ...] = ()
    expected_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    stale_facts: tuple[str, ...] = ()
    absent_terms: tuple[str, ...] = ()
    denied_facts: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


class DatasetError(ValueError):
    pass


def _tup(d: dict[str, Any], key: str) -> tuple[str, ...]:
    v = d.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise DatasetError(f"{d.get('id')}: {key} must be a list of non-empty strings")
    return tuple(v)


def load_dataset(path: Path) -> list[Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise DatasetError("dataset must be a YAML list")
    out: list[Question] = []
    seen: set[str] = set()
    for d in raw:
        qid = d.get("id")
        if not qid or qid in seen:
            raise DatasetError(f"missing or duplicate id: {qid}")
        seen.add(qid)
        q = Question(
            id=qid,
            question=str(d.get("question", "")).strip(),
            category=d.get("category", ""),
            as_user=d.get("as_user", ""),
            split=d.get("split", ""),
            expected_facts=_tup(d, "expected_facts"),
            expected_sources=_tup(d, "expected_sources"),
            forbidden_sources=_tup(d, "forbidden_sources"),
            stale_facts=_tup(d, "stale_facts"),
            absent_terms=_tup(d, "absent_terms"),
            denied_facts=_tup(d, "denied_facts"),
            tags=_tup(d, "tags"),
            note=str(d.get("note", "")),
        )
        _validate(q)
        out.append(q)
    return out


def _validate(q: Question) -> None:
    if not q.question:
        raise DatasetError(f"{q.id}: empty question")
    if q.category not in CATEGORIES:
        raise DatasetError(f"{q.id}: bad category {q.category!r}")
    if q.as_user not in USERS:
        raise DatasetError(f"{q.id}: bad as_user {q.as_user!r}")
    if q.split not in SPLITS:
        raise DatasetError(f"{q.id}: bad split {q.split!r}")
    if q.category == "answerable" and not (q.expected_facts and q.expected_sources):
        raise DatasetError(f"{q.id}: answerable needs expected_facts and expected_sources")
    if q.category == "unanswerable" and not q.absent_terms:
        raise DatasetError(f"{q.id}: unanswerable needs absent_terms (verified absent from corpus)")
    if q.category == "acl_denied" and not (q.forbidden_sources and q.denied_facts):
        raise DatasetError(f"{q.id}: acl_denied needs forbidden_sources and denied_facts")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_source(spec: str) -> tuple[str, int | None]:
    """'hr-leave-policy.pdf#p4' -> ('hr-leave-policy.pdf', 4); 'x.docx' -> ('x.docx', None)."""
    name, _, anchor = spec.partition("#")
    if anchor.startswith("p") and anchor[1:].isdigit():
        return name, int(anchor[1:])
    if anchor:
        raise DatasetError(f"unsupported source anchor: {spec}")
    return name, None
