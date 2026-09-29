import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Keep in sync with Settings.embedding_dim; verified at worker startup.
EMBEDDING_DIM = 384

DOC_STATUSES = ("ready", "failed", "needs_ocr", "empty", "stale")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("collection_id", "source_uri", name="uq_documents_collection_source"),
        CheckConstraint(
            "status in ('ready','failed','needs_ocr','empty','stale')", name="ck_documents_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(50))
    source_uri: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(100))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    # Starts at 1, +1 each time the content changes.
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Always equals the collection ACL narrowed by any document-level hint (never wider).
    acl_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Optional narrowing hint from the source; persisted so ACL changes never widen it.
    acl_hint: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which model produced the chunk embeddings; retrieval must not mix models.
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the source says the content was last modified / took effect (not ingest time).
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        # Every chunk must be citable: a page (PDF) or a section path (everything else).
        CheckConstraint(
            "page_start is not null or section_path is not null", name="ck_chunks_has_anchor"
        ),
        CheckConstraint(
            "page_start is null or page_end >= page_start", name="ck_chunks_page_order"
        ),
        Index("ix_chunks_acl_groups", "acl_groups", postgresql_using="gin"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Denormalised so retrieval filters collection + ACL in one query.
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalised from the document (like acl_groups) so ANN scans filter on chunk columns only.
    embedding_model: Mapped[str] = mapped_column(String(200))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    # Copy of the document ACL, kept in sync in the same transaction (see acl.py).
    acl_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # e.g. {"injection": ["ignore_previous"], "oversize": true}. Names only, never content.
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    connector: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
