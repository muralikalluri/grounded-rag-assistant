import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Chunk, Document
from app.retrieval.types import RetrievedChunk
from tests.support.embedder import FakeEmbedder


class FakeReranker:
    """Scores by query-word overlap; deterministic and offline."""

    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        q = set(query.lower().split())
        return [float(len(q & set(p.lower().split()))) for p in passages], [False] * len(passages)


class WrongLengthReranker:
    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        return [1.0] * (len(passages) + 3), [False] * (len(passages) + 3)


class ExplodingReranker:
    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        raise RuntimeError("model crashed")


class FixedQueryEmbedder(FakeEmbedder):
    """Embeds every query to one fixed unit vector (for adversarial ANN tests)."""

    def __init__(self, vec: list[float]) -> None:
        super().__init__(dim=len(vec))
        self._vec = vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec for _ in texts]


def ids(chunks: list[RetrievedChunk]) -> list[uuid.UUID]:
    return [c.chunk_id for c in chunks]


async def insert_raw_chunks(
    sf: async_sessionmaker[AsyncSession],
    collection_id: uuid.UUID,
    acl: list[str],
    vectors: list[list[float]],
    *,
    prefix: str,
    model: str = "fake-embedder",
    status: str = "ready",
) -> list[uuid.UUID]:
    """Insert one document with ``len(vectors)`` chunks directly (bypasses the pipeline)."""
    async with sf() as s, s.begin():
        doc = Document(
            collection_id=collection_id, source_type="upload", source_uri=f"upload://{prefix}",
            title=prefix, mime_type="text/plain", checksum_sha256=prefix.ljust(64, "0")[:64],
            version=1, acl_groups=acl, status=status, embedding_model=model,
            source_modified_at=datetime(2025, 1, 1, tzinfo=UTC),
        )  # fmt: skip
        s.add(doc)
        await s.flush()
        out = []
        for i, v in enumerate(vectors):
            c = Chunk(
                document_id=doc.id, collection_id=collection_id, ordinal=i,
                text=f"{prefix} chunk {i} filler text", token_count=5, section_path=prefix,
                embedding_model=model, embedding=v, acl_groups=acl, flags={},
            )  # fmt: skip
            s.add(c)
            await s.flush()
            out.append(c.id)
        await s.execute(text("ANALYZE chunks"))
        return out
