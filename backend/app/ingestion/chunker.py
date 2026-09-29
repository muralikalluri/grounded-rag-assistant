"""Structure-aware chunker.

Invariants (see tests/test_chunker.py):
- chunks never cross heading boundaries and end on sentence boundaries; a single sentence
  longer than the cap is hard-split on word boundaries and flagged ``oversize``
- token_count never exceeds ``max_tokens`` (measured with the embedding tokenizer)
- overlap is whole sentences from the previous chunk in the same section, ~overlap_ratio
- paged documents: page_start <= page_end; otherwise section_path is always set
- deterministic: same input, same output; ordinals contiguous from 0
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ingestion.types import ParsedDocument

TokenCounter = Callable[[str], int]

_ABBREVIATIONS = (
    "e.g.", "i.e.", "etc.", "vs.", "no.", "dr.", "mr.", "mrs.", "ms.", "inc.", "ltd.",
    "co.", "approx.", "fig.", "sec.", "st.",
)  # fmt: skip
_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]*[A-Z0-9])")


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: str | None
    flags: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _Sentence:
    text: str
    tokens: int
    page_start: int | None
    page_end: int | None
    oversize: bool = False


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, without splitting after common abbreviations."""
    parts: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        candidate = text[start : m.start() + 1]
        last_word = candidate.split()[-1].lower() if candidate.split() else ""
        if last_word in _ABBREVIATIONS:
            continue
        parts.append(text[start : m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _hard_split(text: str, max_tokens: int, count: TokenCounter) -> list[str]:
    """Split one over-long sentence on word boundaries into pieces within the cap."""
    pieces: list[str] = []
    current: list[str] = []
    for word in text.split():
        trial = " ".join([*current, word])
        if current and count(trial) > max_tokens:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_document(
    doc: ParsedDocument,
    *,
    max_tokens: int,
    overlap_ratio: float,
    count_tokens: TokenCounter,
) -> list[ChunkDraft]:
    overlap_budget = int(max_tokens * overlap_ratio)
    chunks: list[ChunkDraft] = []
    heading_stack: list[tuple[int, str]] = []

    section_sentences: list[_Sentence] = []
    section_path: str = doc.title

    def path() -> str:
        return " > ".join(text for _, text in heading_stack) if heading_stack else doc.title

    def flush_section() -> None:
        if not section_sentences:
            return
        _emit(section_sentences, section_path)
        section_sentences.clear()

    def _emit(sentences: list[_Sentence], sec: str) -> None:
        current: list[_Sentence] = []
        fresh = 0  # sentences in `current` that are not overlap

        def close() -> None:
            nonlocal current, fresh
            if fresh == 0:
                return
            text = " ".join(s.text for s in current)
            pages = [p for s in current for p in (s.page_start, s.page_end) if p is not None]
            flags: dict[str, object] = {}
            if any(s.oversize for s in current):
                flags["oversize"] = True
            chunks.append(
                ChunkDraft(
                    ordinal=len(chunks),
                    text=text,
                    token_count=count_tokens(text),
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    section_path=sec,
                    flags=flags,
                )
            )
            # Seed next chunk with trailing sentences (whole sentences, within budget).
            carry: list[_Sentence] = []
            used = 0
            for s in reversed(current):
                if s.oversize or used + s.tokens > overlap_budget:
                    break
                carry.insert(0, s)
                used += s.tokens
            current = carry
            fresh = 0

        for s in sentences:
            joined = " ".join([*(x.text for x in current), s.text])
            if fresh > 0 and count_tokens(joined) > max_tokens:
                close()
                # After seeding overlap, drop it if it would still not fit with `s`.
                joined = " ".join([*(x.text for x in current), s.text])
                if count_tokens(joined) > max_tokens:
                    current = []
            current.append(s)
            fresh += 1
        close()

    for block in doc.blocks:
        if block.kind == "heading":
            flush_section()
            while heading_stack and heading_stack[-1][0] >= block.level:
                heading_stack.pop()
            heading_stack.append((block.level, block.text))
            section_path = path()
            continue
        section_path = path()
        for raw in split_sentences(block.text):
            tokens = count_tokens(raw)
            if tokens <= max_tokens:
                section_sentences.append(_Sentence(raw, tokens, block.page_start, block.page_end))
            else:
                for piece in _hard_split(raw, max_tokens, count_tokens):
                    section_sentences.append(
                        _Sentence(
                            piece,
                            count_tokens(piece),
                            block.page_start,
                            block.page_end,
                            oversize=True,
                        )
                    )
    flush_section()

    if doc.paged:
        # Paged docs must have a page on every chunk; blocks always carry one.
        chunks = [c for c in chunks if c.page_start is not None]
    return chunks
