import pytest

from app.ingestion.connectors.upload import (
    UploadConnector,
    UploadRejected,
    read_limited,
    resolve_mime,
    sniff_mime,
)
from app.ingestion.types import MIME_DOCX, MIME_HTML, MIME_MD, MIME_PDF, MIME_TXT
from tests.support.builders import BODY, make_docx, make_pdf

LIMIT = 1024 * 1024


async def stream(*parts: bytes):  # type: ignore[no-untyped-def]
    for p in parts:
        yield p


async def test_oversize_upload_aborts_while_streaming_with_413() -> None:
    with pytest.raises(UploadRejected) as exc:
        await read_limited(stream(b"a" * 600, b"b" * 600, b"c" * 10_000_000), max_bytes=1000)
    assert exc.value.status_code == 413


def test_oversize_bytes_rejected_by_connector() -> None:
    with pytest.raises(UploadRejected) as exc:
        UploadConnector("a.txt", b"x" * 101, max_bytes=100)
    assert exc.value.status_code == 413


@pytest.mark.parametrize("name", ["report.pdf", "report.docx", "page.html", "n.md", "n.txt"])
def test_renamed_executable_is_rejected_with_415(name: str) -> None:
    with pytest.raises(UploadRejected) as exc:
        UploadConnector(name, b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00", max_bytes=LIMIT)
    assert exc.value.status_code == 415


def test_extension_content_mismatch_rejected() -> None:
    pdf = make_pdf([[(BODY, 11)]])
    with pytest.raises(UploadRejected) as exc:
        UploadConnector("notes.docx", pdf, max_bytes=LIMIT)
    assert exc.value.status_code == 415
    with pytest.raises(UploadRejected):
        UploadConnector("notes.txt", pdf, max_bytes=LIMIT)


def test_unsupported_extension_rejected() -> None:
    with pytest.raises(UploadRejected) as exc:
        UploadConnector("archive.zip", b"PK\x03\x04", max_bytes=LIMIT)
    assert exc.value.status_code == 415


def test_zip_that_is_not_docx_rejected() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(UploadRejected):
        UploadConnector("x.docx", buf.getvalue(), max_bytes=LIMIT)


def test_valid_types_are_accepted() -> None:
    assert resolve_mime("a.pdf", make_pdf([[(BODY, 11)]])) == MIME_PDF
    assert resolve_mime("a.docx", make_docx([("H", ["p"])])) == MIME_DOCX
    assert resolve_mime("a.html", b"<html><body><p>hi</p></body></html>") == MIME_HTML
    assert resolve_mime("a.md", b"# Title\n\nhello") == MIME_MD
    assert resolve_mime("a.txt", b"hello world") == MIME_TXT
    assert sniff_mime(b"\x00\x01binary") is None


async def test_client_filename_is_reduced_to_base_name() -> None:
    c = UploadConnector("../../etc/passwd.txt", b"hello", max_bytes=LIMIT)
    (item,) = [i async for i in c.list_items(None)]
    assert item.filename == "passwd.txt" and item.uri == "upload://passwd.txt"
    raw = await c.fetch(item)
    assert raw.source_type == "upload" and raw.mime_type == MIME_TXT
