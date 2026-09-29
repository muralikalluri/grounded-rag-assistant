"""Highlight location + page rendering (no database). Correctness is checked on pixels."""

import io
from pathlib import Path

import pymupdf
import pytest

from app.config import Settings
from app.preview.render import (
    PreviewError,
    locate_quote,
    render_page_png,
    safe_pdf_path,
)
from tests.support.builders import make_page_pdf

S = Settings()
SENT = "The retention period for shipment records is seven years."


def write(tmp_path: Path, data: bytes, name: str = "d.pdf") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def dark_pixels(png: bytes, rect: tuple[float, float, float, float]) -> int:
    pix = pymupdf.Pixmap(io.BytesIO(png))
    x0, y0 = int(rect[0] * pix.width), int(rect[1] * pix.height)
    x1, y1 = int(rect[2] * pix.width), int(rect[3] * pix.height)
    return sum(
        1
        for x in range(max(0, x0), min(pix.width, x1))
        for y in range(max(0, y0), min(pix.height, y1))
        if sum(pix.pixel(x, y)[:3]) < 300
    )


def test_quote_is_found_on_the_right_page_and_the_box_covers_text(tmp_path: Path) -> None:
    pdf = write(
        tmp_path,
        make_page_pdf([[""] * 25 + ["Page one filler line."], ["Page two."], [SENT, "More."]]),
    )
    res = locate_quote(
        pdf, [1, 2, 3], "retention period for shipment records", "The ", " is seven", S
    )
    assert res.found and not res.ambiguous and res.method == "exact"
    (seg,) = res.segments
    assert seg.page == 3
    assert all(0 <= v <= 1 for r in seg.rects for v in r)
    png = render_page_png(pdf, 3, 1400)
    assert sum(dark_pixels(png, r) for r in seg.rects) > 50  # the box really sits on the text
    # and nothing dark on the same spot of the wrong page
    assert sum(dark_pixels(render_page_png(pdf, 1, 1400), r) for r in seg.rects) < 5


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_pages_map_boxes_onto_the_rendered_image(tmp_path: Path, rotation: int) -> None:
    pdf = write(tmp_path, make_page_pdf([[SENT]], rotation=rotation))
    res = locate_quote(pdf, [1], "retention period for shipment records", "", "", S)
    assert res.found
    png = render_page_png(pdf, 1, 1400)
    assert sum(dark_pixels(png, r) for r in res.segments[0].rects) > 50


def test_cropped_pages_map_boxes_onto_the_rendered_image(tmp_path: Path) -> None:
    pdf = write(tmp_path, make_page_pdf([[SENT]], crop=(50, 60, 500, 400)))
    res = locate_quote(pdf, [1], "retention period for shipment records", "", "", S)
    assert res.found
    png = render_page_png(pdf, 1, 1400)
    assert sum(dark_pixels(png, r) for r in res.segments[0].rects) > 50


def test_quote_wrapped_over_two_lines_is_highlighted_as_two_boxes(tmp_path: Path) -> None:
    pdf = write(
        tmp_path, make_page_pdf([["The retention period for shipment", "records is seven years."]])
    )
    res = locate_quote(pdf, [1], "retention period for shipment records is seven", "", "", S)
    assert res.found and len(res.segments[0].rects) >= 2


def test_line_break_hyphenation_in_chunk_text_still_matches(tmp_path: Path) -> None:
    pdf = write(
        tmp_path,
        make_page_pdf([["The shipment manifest is an exam-", "ple of a controlled record."]]),
    )
    res = locate_quote(pdf, [1], "manifest is an exam- ple of a controlled record", "", "", S)
    assert res.found


def test_repeated_text_uses_context_and_is_ambiguous_when_context_cannot_decide(
    tmp_path: Path,
) -> None:
    lines = [
        "Alpha rule. Fees are due in thirty days.",
        "Filler.",
        "Beta rule. Fees are due in thirty days.",
    ]
    pdf = write(tmp_path, make_page_pdf([lines]))
    # context distinguishes the second occurrence -> one place, not ambiguous
    res = locate_quote(pdf, [1], "Fees are due in thirty days.", "Beta rule. ", "", S)
    assert res.found and not res.ambiguous
    # no distinguishing context -> refuse to guess
    res2 = locate_quote(pdf, [1], "Fees are due in thirty days.", "", "", S)
    assert not res2.found and res2.ambiguous and res2.segments == []


def test_text_that_is_not_on_the_page_is_not_found_and_never_guessed(tmp_path: Path) -> None:
    pdf = write(tmp_path, make_page_pdf([[SENT]]))
    res = locate_quote(
        pdf, [1], "an entirely different sentence about pallets and forklifts today", "", "", S
    )
    assert not res.found and res.segments == []
    # an inserted stray word must not be papered over by the window fallback
    stray = "The retention period for STRAY shipment records is seven years in every region"
    assert not locate_quote(pdf, [1], stray, "", "", S).found


def test_quote_continuing_onto_the_next_page_uses_window_fallback_in_order(tmp_path: Path) -> None:
    p1 = [
        "Filler.",
        "Employees may carry forward a maximum of ten days of unused annual leave into",
    ]
    p2 = ["the next leave year and any excess days above this limit are forfeited on the last day."]
    pdf = write(tmp_path, make_page_pdf([p1, p2]))
    quote = (
        "Employees may carry forward a maximum of ten days of unused annual leave into "
        "the next leave year and any excess days above this limit are forfeited on the last day"
    )
    res = locate_quote(pdf, [1, 2], quote, "", "", S)
    assert res.found and res.method == "windows"
    assert [s.page for s in res.segments] == [1, 2]


def test_render_size_is_capped_by_config_not_by_the_page_size(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=14400, height=14400).insert_text((100, 100), "huge page", fontsize=200)
    pdf = write(tmp_path, doc.tobytes())
    pix = pymupdf.Pixmap(io.BytesIO(render_page_png(pdf, 1, 800)))
    assert max(pix.width, pix.height) <= 801


def test_render_rejects_pages_outside_the_document(tmp_path: Path) -> None:
    pdf = write(tmp_path, make_page_pdf([[SENT]]))
    for bad in (0, 2, 999999):
        with pytest.raises(PreviewError):
            render_page_png(pdf, bad, 800)


def test_safe_path_rejects_traversal_symlinks_and_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "files"
    (root / "c").mkdir(parents=True)
    ok = root / "c" / "a.pdf"
    ok.write_bytes(make_page_pdf([[SENT]]))
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-secret")
    (root / "c" / "link.pdf").symlink_to(outside)
    cfg = Settings(storage_dir=root)
    assert safe_pdf_path(cfg, "c/a.pdf") == ok.resolve()
    for bad in (
        "../secret.pdf",
        "c/../../secret.pdf",
        "/etc/passwd",
        "c/link.pdf",
        "c/missing.pdf",
    ):
        with pytest.raises(PreviewError):
            safe_pdf_path(cfg, bad)
