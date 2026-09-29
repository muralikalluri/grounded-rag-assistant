import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingestion.acl import effective_acl, get_collection_acl
from app.ingestion.chunker import chunk_document
from app.ingestion.embedding import Embedder, embed_async
from app.ingestion.flags import compile_patterns, injection_matches
from app.ingestion.parsers import parse_document
from app.ingestion.storage import store_raw_file
from app.ingestion.types import ParsedDocument, ParseError, RawDocument
from app.models import Chunk, Collection, Document

logger = logging.getLogger(__name__)

Action = Literal["created", "updated", "skipped", "failed"]
_TERMINAL = ("ready", "needs_ocr", "empty")


@dataclass(frozen=True)
class IngestOutcome:
    action: Action
    document_id: uuid.UUID | None
    status: str
    chunks_created: int = 0
    error: str | None = None


class IngestPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        settings: Settings,
    ) -> None:
        self._sf = session_factory
        self._embedder = embedder
        self._settings = settings
        self._patterns = compile_patterns(settings.injection_patterns)

    async def ingest(self, collection_id: uuid.UUID, raw: RawDocument) -> IngestOutcome:
        """Ingest one document idempotently.

        One transaction per document, serialised per (collection, source_uri) by an advisory
        lock: a document is either fully old or fully new. Unchanged content is a no-op.
        """
        checksum = hashlib.sha256(raw.content).hexdigest()
        async with self._sf() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"{collection_id}:{raw.item.uri}"},
            )
            # FOR SHARE for the whole transaction: a concurrent set_collection_acl (which takes
            # FOR UPDATE) waits for us, so the ACL we copy onto chunks cannot go stale.
            locked = await session.scalar(
                select(Collection.id)
                .where(Collection.id == collection_id)
                .with_for_update(read=True)
            )
            if locked is None:
                raise LookupError("collection not found")
            existing = await session.scalar(
                select(Document).where(
                    Document.collection_id == collection_id,
                    Document.source_uri == raw.item.uri,
                )
            )
            if (
                existing is not None
                and existing.checksum_sha256 == checksum
                and existing.status in _TERMINAL
                and (
                    existing.status != "ready"
                    or existing.embedding_model == self._embedder.model_name
                )
            ):
                return IngestOutcome("skipped", existing.id, existing.status)

            parsed_or_error = await self._parse(raw)
            if isinstance(parsed_or_error, str):
                return await self._record_failure(
                    session, collection_id, raw, checksum, existing, parsed_or_error
                )
            parsed = parsed_or_error

            drafts = chunk_document(
                parsed,
                max_tokens=self._settings.chunk_max_tokens,
                overlap_ratio=self._settings.chunk_overlap_ratio,
                count_tokens=self._embedder.count_tokens,
            )
            vectors = await embed_async(self._embedder, [d.text for d in drafts]) if drafts else []
            if any(len(v) != self._settings.embedding_dim for v in vectors):
                raise RuntimeError("embedding dimension does not match configuration")

            if parsed.needs_ocr:
                status = "needs_ocr"
                error: str | None = "pdf has no text layer; OCR is not enabled"
            elif not drafts:
                status, error = "empty", "no extractable text"
            else:
                status, error = "ready", None

            collection_groups = await get_collection_acl(session, collection_id)
            acl = effective_acl(collection_groups, raw.acl_hint)
            storage_path = await asyncio.to_thread(
                store_raw_file,
                self._settings,
                collection_id,
                checksum,
                raw.item.filename,
                raw.content,
            )

            if existing is None:
                doc = Document(
                    collection_id=collection_id,
                    source_type=raw.source_type,
                    source_uri=raw.item.uri,
                    version=1,
                )
                session.add(doc)
                action: Action = "created"
            else:
                doc = existing
                await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                doc.version = doc.version + 1
                action = "updated"
            doc.title = parsed.title
            doc.mime_type = raw.mime_type
            doc.checksum_sha256 = checksum
            doc.acl_hint = raw.acl_hint
            doc.acl_groups = acl
            doc.status = status
            doc.error = error
            doc.embedding_model = self._embedder.model_name if drafts else None
            doc.storage_path = storage_path
            doc.source_modified_at = raw.item.modified_at
            await session.flush()

            for draft, vector in zip(drafts, vectors, strict=True):
                flags = dict(draft.flags)
                matched = injection_matches(draft.text, self._patterns)
                if matched:
                    flags["injection"] = matched
                session.add(
                    Chunk(
                        document_id=doc.id,
                        collection_id=collection_id,
                        ordinal=draft.ordinal,
                        text=draft.text,
                        token_count=draft.token_count,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        section_path=draft.section_path,
                        embedding_model=self._embedder.model_name,
                        embedding=vector,
                        acl_groups=acl,
                        flags=flags,
                    )
                )
            await session.flush()
            logger.info(
                "ingested document",
                extra={"document_id": str(doc.id), "action": action, "chunks": len(drafts)},
            )
            return IngestOutcome(action, doc.id, status, chunks_created=len(drafts), error=error)

    async def _parse(self, raw: RawDocument) -> ParsedDocument | str:
        """Parse off the event loop. Returns an error string (no document text) on failure."""
        fallback = raw.item.filename.rsplit(".", 1)[0]
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    parse_document, raw.mime_type, raw.content, fallback, self._settings
                ),
                timeout=self._settings.parse_timeout_seconds,
            )
        except ParseError as exc:
            return str(exc)
        except TimeoutError:
            return "parse timed out"
        except Exception as exc:
            logger.warning("unexpected parse failure", exc_info=True)
            return f"unexpected parse failure: {type(exc).__name__}"

    async def _record_failure(
        self,
        session: AsyncSession,
        collection_id: uuid.UUID,
        raw: RawDocument,
        checksum: str,
        existing: Document | None,
        error: str,
    ) -> IngestOutcome:
        if existing is not None:
            # Keep serving the last good version; just record why the new one was rejected.
            existing.error = error
            return IngestOutcome("failed", existing.id, existing.status, error=error)
        doc = Document(
            collection_id=collection_id,
            source_type=raw.source_type,
            source_uri=raw.item.uri,
            title=raw.item.filename,
            mime_type=raw.mime_type,
            checksum_sha256=checksum,
            version=1,
            acl_groups=[],
            acl_hint=raw.acl_hint,
            status="failed",
            error=error,
        )
        session.add(doc)
        await session.flush()
        return IngestOutcome("failed", doc.id, "failed", error=error)
