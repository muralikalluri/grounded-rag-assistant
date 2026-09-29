"""Server-side PDF page rendering and quote location (PyMuPDF). Pure functions, blocking:
call through ``asyncio.to_thread``. Nothing here checks authorisation; ``PreviewService`` does,
before any file is touched."""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.config import Settings


class PreviewError(Exception):
    """Generic failure (bad file, render error). Message never contains document text."""


@dataclass
class Segment:
    page: int  # 1-based
    # Rectangles normalised to 0..1 of the rendered page image (x0, y0, x1, y1).
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass
class LocateResult:
    found: bool
    ambiguous: bool = False
    segments: list[Segment] = field(default_factory=list)
    method: str = "none"


def safe_pdf_path(settings: Settings, storage_path: str) -> Path:
    """Resolve a stored path strictly inside storage_dir; reject symlinks and traversal."""
    root = settings.storage_dir.resolve()
    candidate = settings.storage_dir / storage_path
    if candidate.is_symlink():
        raise PreviewError("unsafe path")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise PreviewError("file not available")
    return resolved


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _dehyphenate(text: str) -> str:
    # The chunker joined lines with spaces ("exam- ple"); PDF search dehyphenates itself.
    return re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", text)


def _variants(needle: str) -> list[str]:
    out: list[str] = []
    for v in (_collapse(needle), _dehyphenate(_collapse(needle))):
        if v and v not in out:
            out.append(v)
    return out


def _norm_rect(page: pymupdf.Page, r: pymupdf.Rect) -> tuple[float, float, float, float]:
    # search_for returns unrotated page space; rendering (and the DOM overlay) use the rotated one.
    rr = r * page.rotation_matrix
    w, h = page.rect.width, page.rect.height
    x0, x1 = sorted((rr.x0, rr.x1))
    y0, y1 = sorted((rr.y0, rr.y1))
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    return (clamp(x0 / w), clamp(y0 / h), clamp(x1 / w), clamp(y1 / h))


def _page_count_of(page: pymupdf.Page, variants: list[str]) -> int:
    text = _collapse(page.get_text())
    hay = [text, _dehyphenate(text)]
    return max((h.lower().count(v.lower()) for h in hay for v in variants), default=0)


def locate_quote(
    pdf_path: Path,
    pages: list[int],
    needle: str,
    before: str,
    after: str,
    settings: Settings,
) -> LocateResult:
    """Find the quoted span on ``pages`` (the cited chunk's pages) and return where it is.

    Never guesses: if the exact span is not found and the word-window fallback cannot cover
    most of it in reading order, the result is ``found=False``.
    """
    variants = _variants(needle)
    if not variants:
        return LocateResult(False)
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PreviewError("could not open pdf") from exc
    with doc:
        valid = [p for p in pages if 1 <= p <= doc.page_count]
        # 1) the whole quote on a single page (the normal case)
        segments: list[Segment] = []
        ambiguous = False
        for pno in valid:
            page = doc.load_page(pno - 1)
            rects: list[pymupdf.Rect] = []
            used = ""
            for v in variants:
                rects = page.search_for(v)[: settings.preview_hit_max]
                if rects:
                    used = v
                    break
            if not rects:
                continue
            occurrences = _page_count_of(page, variants)
            if occurrences > 1:
                # Same text more than once on the page: use surrounding chunk text to pick one.
                ctx = _collapse(before[-settings.preview_context_chars :] + " " + used + " "
                                + after[: settings.preview_context_chars])  # fmt: skip
                ctx_hits = page.search_for(ctx)[: settings.preview_hit_max]
                if ctx_hits:
                    region = ctx_hits[0]
                    for extra in ctx_hits[1:]:
                        region |= extra
                    rects = [r for r in rects if r.intersects(region)] or rects
                    if len(ctx_hits) > 1:
                        ambiguous = True
                else:
                    ambiguous = True
            segments.append(Segment(pno, [_norm_rect(page, r) for r in rects]))
        if segments:
            if ambiguous:
                # Do not present a guessed occurrence as the evidence.
                return LocateResult(False, True, [], "ambiguous")
            return LocateResult(True, False, segments[:1], "exact")

        # 2) fallback: word windows, for quotes wrapped across lines/pages in awkward ways
        words = _collapse(needle).split()
        size = settings.preview_fallback_window_words
        if len(words) < size:
            return LocateResult(False)
        windows = [" ".join(words[i : i + size]) for i in range(0, len(words) - size + 1, size)]
        per_page: dict[int, list[tuple[int, list[pymupdf.Rect]]]] = {}
        hit_windows = 0
        for pno in valid:
            page = doc.load_page(pno - 1)
            for wi, w in enumerate(windows):
                hits = page.search_for(w)[: settings.preview_fallback_max_hits]
                if len(hits) == 1:  # a window that occurs several times proves nothing
                    per_page.setdefault(pno, []).append((wi, hits))
                    hit_windows += 1
        if hit_windows < settings.preview_fallback_min_coverage * len(windows):
            return LocateResult(False)
        order = [wi for pno in sorted(per_page) for wi, _ in per_page[pno]]
        if order != sorted(order):
            return LocateResult(False)  # hits out of reading order: not one contiguous quote
        segs = []
        for pno in sorted(per_page):
            page = doc.load_page(pno - 1)
            segs.append(Segment(pno, [_norm_rect(page, r) for _, hs in per_page[pno] for r in hs]))
        return LocateResult(True, False, segs, "windows")


def render_page_png(pdf_path: Path, page_no: int, max_px: int) -> bytes:
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PreviewError("could not open pdf") from exc
    with doc:
        if not 1 <= page_no <= doc.page_count:
            raise PreviewError("page out of range")
        page = doc.load_page(page_no - 1)
        longest = max(page.rect.width, page.rect.height)
        if longest <= 0:
            raise PreviewError("empty page")
        zoom = max_px / longest  # pixel budget fixed by config, never by the client
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return bytes(pix.tobytes("png"))
