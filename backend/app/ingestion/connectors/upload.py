"""Upload connector.

Upload is push, not pull: each upload is wrapped as a one-item source. Validation is
content-sniffing (not the client's Content-Type) cross-checked with the file extension.
"""

import io
import zipfile
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from pathlib import PurePath

from app.ingestion.types import (
    MIME_DOCX,
    MIME_HTML,
    MIME_MD,
    MIME_PDF,
    MIME_TXT,
    RawDocument,
    SourceItem,
)

_EXT_TO_MIME = {
    ".pdf": MIME_PDF,
    ".docx": MIME_DOCX,
    ".html": MIME_HTML,
    ".htm": MIME_HTML,
    ".md": MIME_MD,
    ".txt": MIME_TXT,
}
_TEXT_FAMILY = {MIME_MD, MIME_TXT}
_HTML_MARKERS = (
    b"<html",
    b"<body",
    b"<!doctype html",
    b"<p>",
    b"<p ",
    b"<h1",
    b"<div",
    b"<article",
)


class UploadRejected(Exception):
    """Carries the HTTP status the API layer should return."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def sniff_mime(content: bytes) -> str | None:
    """Return the detected mime type, or None if it is not an accepted type."""
    if content.startswith(b"%PDF-"):
        return MIME_PDF
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "word/document.xml" in zf.namelist():
                    return MIME_DOCX
        except zipfile.BadZipFile:
            return None
        return None
    head = content[:4096]
    if b"\x00" in head:
        return None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte char cut at the 4096 boundary is fine; anything earlier is not.
        if exc.start < len(head) - 4:
            return None
    lowered = head.lower()
    if any(m in lowered for m in _HTML_MARKERS):
        return MIME_HTML
    return "text/*"


def resolve_mime(filename: str, content: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    expected = _EXT_TO_MIME.get(ext)
    if expected is None:
        raise UploadRejected(415, "unsupported file extension")
    sniffed = sniff_mime(content)
    if sniffed is None:
        raise UploadRejected(415, "file content is not an accepted type")
    if expected in _TEXT_FAMILY:
        ok = sniffed in ("text/*", MIME_HTML)  # plain text (markdown may contain html)
    else:
        ok = sniffed == expected
    if not ok:
        raise UploadRejected(415, "file content does not match its extension")
    return expected


async def read_limited(stream: AsyncIterable[bytes], max_bytes: int) -> bytes:
    """Read an upload stream, aborting as soon as it exceeds the limit."""
    buf = bytearray()
    async for chunk in stream:
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise UploadRejected(413, "file too large")
    return bytes(buf)


class UploadConnector:
    name = "upload"

    def __init__(
        self,
        filename: str,
        content: bytes,
        *,
        max_bytes: int,
        modified_at: datetime | None = None,
        acl_hint: list[str] | None = None,
    ) -> None:
        if len(content) > max_bytes:
            raise UploadRejected(413, "file too large")
        # Only the base name is kept, and only as metadata.
        self._filename = PurePath(filename.replace("\\", "/")).name or "upload"
        self._content = content
        self._mime = resolve_mime(self._filename, content)
        self._modified_at = modified_at
        self._acl_hint = acl_hint
        self._item = SourceItem(
            uri=f"upload://{self._filename}", filename=self._filename, modified_at=modified_at
        )

    async def list_items(self, since: datetime | None) -> AsyncIterator[SourceItem]:
        if since is None or self._modified_at is None or self._modified_at > since:
            yield self._item

    async def fetch(self, item: SourceItem) -> RawDocument:
        return RawDocument(
            item=item,
            content=self._content,
            mime_type=self._mime,
            source_type=self.name,
            acl_hint=self._acl_hint,
        )
