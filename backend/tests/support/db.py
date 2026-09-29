import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.acl import set_collection_acl
from app.ingestion.types import MIME_PDF, MIME_TXT, RawDocument, SourceItem
from app.models import Chunk, Collection, Document


async def make_collection(
    sf: async_sessionmaker[AsyncSession], name: str, groups: list[str]
) -> uuid.UUID:
    async with sf() as s, s.begin():
        c = Collection(name=name, description="")
        s.add(c)
        await s.flush()
        await set_collection_acl(s, c.id, groups)
        return c.id


def raw_text(name: str, text: str, *, hint: list[str] | None = None) -> RawDocument:
    return RawDocument(
        item=SourceItem(uri=f"upload://{name}", filename=name),
        content=text.encode(),
        mime_type=MIME_TXT,
        source_type="upload",
        acl_hint=hint,
    )


def raw_pdf(name: str, content: bytes) -> RawDocument:
    return RawDocument(
        item=SourceItem(uri=f"upload://{name}", filename=name),
        content=content,
        mime_type=MIME_PDF,
        source_type="upload",
    )


async def chunk_ids(sf: async_sessionmaker[AsyncSession], doc_id: uuid.UUID) -> list[uuid.UUID]:
    async with sf() as s:
        rows = await s.scalars(
            select(Chunk.id).where(Chunk.document_id == doc_id).order_by(Chunk.ordinal)
        )
        return list(rows.all())


async def get_doc(sf: async_sessionmaker[AsyncSession], doc_id: uuid.UUID) -> Document:
    async with sf() as s:
        doc = await s.get(Document, doc_id)
        assert doc is not None
        return doc


SENTENCES = " ".join(f"Sentence number {i} talks about topic {i} in detail." for i in range(60))
