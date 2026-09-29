import re
import statistics
from dataclasses import dataclass

import pymupdf

from app.config import Settings
from app.ingestion.types import Block, ParsedDocument, ParseError

_HEADING_SIZE_RATIO = 1.15
_MAX_HEADING_CHARS = 150
_EDGE_FRACTION = 0.08
_MIN_PAGES_FOR_REPEAT_DETECTION = 2
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d+(\s*(/|of)\s*\d+)?$", re.IGNORECASE)
_SENTENCE_END = (".", "!", "?", ":", '"', "”", ")", ";")
_MIN_TEXT_CHARS = 20


@dataclass
class _Raw:
    page: int
    y0: float
    y1: float
    page_height: float
    text: str
    size: float
    lines: int

    @property
    def in_edge(self) -> bool:
        return self.y1 <= self.page_height * _EDGE_FRACTION or self.y0 >= self.page_height * (
            1 - _EDGE_FRACTION
        )


def _extract_raw(doc: pymupdf.Document) -> list[_Raw]:
    out: list[_Raw] = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        height = float(page.rect.height)
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            parts: list[str] = []
            weighted = 0.0
            chars = 0
            for line in block["lines"]:
                line_text = "".join(span["text"] for span in line["spans"]).strip()
                if not line_text:
                    continue
                parts.append(line_text)
                for span in line["spans"]:
                    n = len(span["text"].strip())
                    weighted += span["size"] * n
                    chars += n
            if chars == 0:
                continue
            text = " ".join(" ".join(parts).split())
            x0, y0, x1, y1 = block["bbox"]
            out.append(_Raw(page.number + 1, y0, y1, height, text, weighted / chars, len(parts)))
    return out


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower())


def _drop_repeated_edges(raws: list[_Raw], page_count: int) -> list[_Raw]:
    # A bare page number in the top/bottom margin is never content, whatever the page count.
    raws = [r for r in raws if not (r.in_edge and _PAGE_NUMBER.match(r.text.strip()))]
    if page_count < _MIN_PAGES_FOR_REPEAT_DETECTION:
        return raws
    pages_by_key: dict[str, set[int]] = {}
    for r in raws:
        if r.in_edge:
            pages_by_key.setdefault(_norm(r.text), set()).add(r.page)
    repeated = {
        k for k, pages in pages_by_key.items() if len(pages) >= 2 and len(pages) >= page_count / 2
    }
    return [r for r in raws if not (r.in_edge and _norm(r.text) in repeated)]


def parse_pdf(content: bytes, fallback_title: str, settings: Settings) -> ParsedDocument:
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ParseError("could not open pdf") from exc
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise ParseError("pdf is encrypted")
        page_count = doc.page_count
        if page_count > settings.pdf_max_pages:
            raise ParseError("pdf has too many pages")
        try:
            raws = _drop_repeated_edges(_extract_raw(doc), page_count)
        except Exception as exc:
            raise ParseError("could not extract pdf text") from exc
        meta_title = (doc.metadata or {}).get("title") or ""

    title = meta_title.strip() or fallback_title
    if sum(len(r.text) for r in raws) < _MIN_TEXT_CHARS:
        return ParsedDocument(
            title=title, paged=True, page_count=page_count, needs_ocr=page_count > 0
        )

    # Body size = char-weighted median of block font sizes.
    sizes = [round(r.size, 1) for r in raws for _ in range(max(1, len(r.text) // 20))]
    body = statistics.median(sizes)
    heading_sizes = sorted(
        {
            round(r.size, 1)
            for r in raws
            if r.size >= body * _HEADING_SIZE_RATIO and len(r.text) <= _MAX_HEADING_CHARS
        },
        reverse=True,
    )
    level_of = {s: min(i + 1, 6) for i, s in enumerate(heading_sizes)}

    blocks: list[Block] = []
    for r in raws:
        size = round(r.size, 1)
        if size in level_of and r.lines <= 2:
            blocks.append(
                Block("heading", r.text, level=level_of[size], page_start=r.page, page_end=r.page)
            )
            continue
        prev = blocks[-1] if blocks else None
        # A paragraph that runs over a page break continues on the next page.
        if (
            prev is not None
            and prev.kind == "paragraph"
            and prev.page_end is not None
            and r.page == prev.page_end + 1
            and not prev.text.endswith(_SENTENCE_END)
            and r.text[:1].islower()
        ):
            blocks[-1] = Block(
                "paragraph", f"{prev.text} {r.text}", page_start=prev.page_start, page_end=r.page
            )
        else:
            blocks.append(Block("paragraph", r.text, page_start=r.page, page_end=r.page))

    if title == fallback_title:
        top = next((b for b in blocks if b.kind == "heading" and b.level == 1), None)
        if top is not None:
            title = top.text
    return ParsedDocument(title=title, blocks=blocks, paged=True, page_count=page_count)
