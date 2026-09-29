import re

from hypothesis import given, settings
from hypothesis import strategies as st

from app.ingestion.chunker import chunk_document, split_sentences
from app.ingestion.types import Block, ParsedDocument


def count(text: str) -> int:
    return len(text.split())


def run(doc: ParsedDocument, max_tokens: int = 40, overlap: float = 0.15):  # type: ignore[no-untyped-def]
    return chunk_document(doc, max_tokens=max_tokens, overlap_ratio=overlap, count_tokens=count)


def sentence(i: int, words: int = 8) -> str:
    return f"Sentence{i} " + " ".join(f"w{i}x{j}" for j in range(words - 1)) + "."


def test_split_sentences_respects_abbreviations() -> None:
    text = "Use e.g. a laptop. See No. 5 for details. Dr. Smith agrees! Really? Yes."
    assert split_sentences(text) == [
        "Use e.g. a laptop.",
        "See No. 5 for details.",
        "Dr. Smith agrees!",
        "Really?",
        "Yes.",
    ]


def test_never_crosses_heading_boundaries() -> None:
    doc = ParsedDocument(
        title="Doc",
        blocks=[
            Block("heading", "Alpha", level=1),
            Block("paragraph", " ".join(sentence(i) for i in range(3))),
            Block("heading", "Beta", level=1),
            Block("paragraph", " ".join(sentence(i) for i in range(10, 13))),
        ],
    )
    chunks = run(doc, max_tokens=200)
    assert [c.section_path for c in chunks] == ["Alpha", "Beta"]
    assert "Sentence0" in chunks[0].text and "Sentence10" not in chunks[0].text
    assert "Sentence10" in chunks[1].text and "Sentence0" not in chunks[1].text


def test_section_path_nests_headings() -> None:
    doc = ParsedDocument(
        title="Leave Policy",
        blocks=[
            Block("heading", "Leave Policy", level=1),
            Block("heading", "Annual Leave", level=2),
            Block("heading", "Carry Forward", level=3),
            Block("paragraph", "Ten days may be carried forward."),
            Block("heading", "Sick Leave", level=2),
            Block("paragraph", "Twelve days are paid."),
        ],
    )
    paths = [c.section_path for c in run(doc, max_tokens=200)]
    assert paths == [
        "Leave Policy > Annual Leave > Carry Forward",
        "Leave Policy > Sick Leave",
    ]


def test_content_before_first_heading_falls_back_to_title() -> None:
    doc = ParsedDocument(title="Plain Notes", blocks=[Block("paragraph", "Just some text here.")])
    (chunk,) = run(doc)
    assert chunk.section_path == "Plain Notes"


def test_chunks_end_on_sentence_boundaries_and_respect_cap() -> None:
    text = " ".join(sentence(i) for i in range(30))
    chunks = run(ParsedDocument("T", [Block("paragraph", text)]), max_tokens=40)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 40
        assert c.text.endswith(".")
        assert re.match(r"Sentence\d+ ", c.text)


def test_overlap_is_whole_sentences_from_previous_chunk() -> None:
    text = " ".join(sentence(i) for i in range(30))
    chunks = run(ParsedDocument("T", [Block("paragraph", text)]), max_tokens=40, overlap=0.25)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        first = split_sentences(nxt.text)[0]
        assert first in prev.text  # overlap sentence carried over
        assert nxt.token_count <= 40


def test_oversize_sentence_is_split_and_flagged() -> None:
    giant = " ".join(f"word{i}" for i in range(120)) + "."
    chunks = run(ParsedDocument("T", [Block("paragraph", giant)]), max_tokens=40)
    assert len(chunks) >= 3
    assert all(c.token_count <= 40 for c in chunks)
    assert all(c.flags.get("oversize") for c in chunks)


def test_page_range_spans_pages_and_is_ordered() -> None:
    doc = ParsedDocument(
        title="P",
        paged=True,
        page_count=3,
        blocks=[
            Block("paragraph", "Runs across a page break. It ends here.", page_start=2, page_end=3),
        ],
    )
    (chunk,) = run(doc, max_tokens=200)
    assert (chunk.page_start, chunk.page_end) == (2, 3)


def test_empty_document_has_no_chunks() -> None:
    assert run(ParsedDocument("Empty", [])) == []
    assert run(ParsedDocument("Empty", [Block("heading", "Only heading", level=1)])) == []


def test_deterministic() -> None:
    doc = ParsedDocument("T", [Block("paragraph", " ".join(sentence(i) for i in range(25)))])
    assert run(doc) == run(doc)


@settings(max_examples=60, deadline=None)
@given(
    n_sections=st.integers(1, 4),
    n_sentences=st.integers(1, 25),
    words=st.integers(3, 30),
    max_tokens=st.integers(30, 80),
)
def test_invariants_hold_for_arbitrary_documents(
    n_sections: int, n_sentences: int, words: int, max_tokens: int
) -> None:
    blocks: list[Block] = []
    for s in range(n_sections):
        blocks.append(Block("heading", f"Sec{s}", level=1))
        blocks.append(
            Block(
                "paragraph",
                " ".join(sentence(s * 100 + i, words) for i in range(n_sentences)),
                page_start=s + 1,
                page_end=s + 1,
            )
        )
    doc = ParsedDocument("Doc", blocks, paged=True, page_count=n_sections)
    chunks = run(doc, max_tokens=max_tokens)
    assert chunks
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.token_count <= max_tokens
        assert c.section_path
        assert c.page_start is not None and c.page_end is not None
        assert c.page_start <= c.page_end
        # A chunk only contains sentences of a single section.
        sections = {m for m in re.findall(r"Sentence(\d+)", c.text)}
        assert len({int(x) // 100 for x in sections}) == 1
    # Every sentence appears in at least one chunk (nothing lost).
    joined = " ".join(c.text for c in chunks)
    for s in range(n_sections):
        for i in range(n_sentences):
            assert f"Sentence{s * 100 + i} " in joined
