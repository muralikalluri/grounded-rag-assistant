import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.principal import Principal
from app.config import Settings
from app.preview.render import (
    LocateResult,
    PreviewError,
    locate_quote,
    render_page_png,
    safe_pdf_path,
)
from app.retrieval.acl_filter import fetch_authorized_chunk

PDF = "application/pdf"


class NotFound(Exception):
    """Every authorisation or lookup failure. Deliberately carries no detail."""


class BadRequest(Exception):
    pass


@dataclass
class ChunkView:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    doc_title: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    text: str
    is_pdf: bool
    source_date: str | None
    flags: dict[str, Any]


class PreviewService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._sf = session_factory
        self._s = settings
        self._sem = asyncio.Semaphore(settings.preview_render_concurrency)

    async def _authorize(
        self, principal: Principal, document_id: uuid.UUID, chunk_id: uuid.UUID
    ) -> Any:
        """ACL check FIRST. No file I/O happens before this returns a row."""
        async with self._sf() as session:
            row = await fetch_authorized_chunk(
                session, principal, document_id, chunk_id, self._s.embedding_model
            )
        if row is None:
            raise NotFound
        return row

    async def get_chunk(
        self, principal: Principal, document_id: uuid.UUID, chunk_id: uuid.UUID
    ) -> ChunkView:
        row = await self._authorize(principal, document_id, chunk_id)
        modified = row.source_modified_at
        return ChunkView(
            chunk_id=row.chunk_id, document_id=row.document_id, doc_title=row.doc_title,
            section_path=row.section_path, page_start=row.page_start, page_end=row.page_end,
            text=row.text, is_pdf=row.mime_type == PDF,
            source_date=modified.date().isoformat() if modified else None,
            flags=dict(row.flags or {}),
        )  # fmt: skip

    async def locate(
        self,
        principal: Principal,
        document_id: uuid.UUID,
        chunk_id: uuid.UUID,
        start: int,
        end: int,
    ) -> LocateResult:
        row = await self._authorize(principal, document_id, chunk_id)
        if row.mime_type != PDF or row.page_start is None:
            raise NotFound
        # start/end are not trusted: the needle is always cut from the server's chunk text.
        if not (0 <= start < end <= len(row.text)) or end - start > self._s.quote_max_chars:
            raise BadRequest
        path = self._path(row)
        pages = list(range(row.page_start, (row.page_end or row.page_start) + 1))
        result: LocateResult = await self._run(
            locate_quote,
            path, pages, row.text[start:end], row.text[:start], row.text[end:], self._s,
        )  # fmt: skip
        return result

    async def page_png(
        self, principal: Principal, document_id: uuid.UUID, chunk_id: uuid.UUID, page: int
    ) -> bytes:
        row = await self._authorize(principal, document_id, chunk_id)
        if row.mime_type != PDF or row.page_start is None:
            raise NotFound
        # Only the cited chunk's pages: a rendered page also shows headers, images and other
        # content that never became a chunk, so document-level ACL alone is not enough here.
        if not row.page_start <= page <= (row.page_end or row.page_start):
            raise NotFound
        path = self._path(row)
        cache = (
            self._s.storage_dir
            / "preview-cache"
            / f"{row.checksum_sha256}-{page}-{self._s.preview_max_px}.png"
        )
        cached = await asyncio.to_thread(_read_if_exists, cache)  # ACL already passed
        if cached is not None:
            return cached
        png: bytes = await self._run(render_page_png, path, page, self._s.preview_max_px)
        await asyncio.to_thread(_write_atomic, cache, png)
        return png

    def _path(self, row: Any) -> Path:
        if not row.storage_path:
            raise NotFound
        try:
            return safe_pdf_path(self._s, row.storage_path)
        except PreviewError:
            raise NotFound from None

    async def _run(self, fn: Any, *args: Any) -> Any:
        """Run blocking PDF work in a thread, with bounded concurrency.

        Threads cannot be cancelled, so the semaphore slot is released only when the thread
        actually finishes (not when we stop waiting for it): a slow or hostile PDF cannot pile up
        more than ``preview_render_concurrency`` running renders.
        """
        timeout = self._s.preview_render_timeout_seconds
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        except TimeoutError:
            raise NotFound from None
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        fut.add_done_callback(lambda _f: self._sem.release())
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except PreviewError:
            raise NotFound from None
        except TimeoutError:
            raise NotFound from None


def _read_if_exists(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    """Best-effort cache write: unique temp name, atomic rename, never fails the request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: readers see the old file or the complete new one
    except OSError:
        tmp.unlink(missing_ok=True)  # a concurrent writer won; the cache is an optimisation only
