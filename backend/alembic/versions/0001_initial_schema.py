"""initial schema

Revision ID: 0001
Revises:
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "collections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "collection_acl",
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("group_name", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collection_id", "group_name"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("acl_groups", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("acl_hint", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('ready','failed','needs_ocr','empty','stale')", name="ck_documents_status"
        ),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "source_uri", name="uq_documents_collection_source"),
    )
    op.create_index(
        op.f("ix_documents_collection_id"), "documents", ["collection_id"], unique=False
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("connector", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ingestion_jobs_collection_id"), "ingestion_jobs", ["collection_id"], unique=False
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("acl_groups", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "page_start is not null or section_path is not null", name="ck_chunks_has_anchor"
        ),
        sa.CheckConstraint(
            "page_start is null or page_end >= page_start", name="ck_chunks_page_order"
        ),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    )
    op.create_index(
        "ix_chunks_acl_groups", "chunks", ["acl_groups"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_chunks_collection_id"), "chunks", ["collection_id"], unique=False)
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False)
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks", postgresql_using="gin")
    op.drop_index(
        "ix_chunks_embedding_hnsw",
        table_name="chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_collection_id"), table_name="chunks")
    op.drop_index("ix_chunks_acl_groups", table_name="chunks", postgresql_using="gin")
    op.drop_table("chunks")
    op.drop_index(op.f("ix_ingestion_jobs_collection_id"), table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index(op.f("ix_documents_collection_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_table("collection_acl")
    op.drop_table("collections")
