"""The ONLY module that emits ``FROM chunks`` for reads.

Every retrieval query is built here and always carries the same three filters:
ACL overlap, collection scope, and the current embedding model. There is no "skip ACL" flag and
no role-based bypass (admins see only what their groups allow). A static test scans ``app/`` for
other chunk reads, and a runtime test checks every executed chunk SELECT contains the filters.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal

SCOPE_WHERE = """
    c.acl_groups && CAST(:groups AS text[])
    AND c.collection_id = ANY(CAST(:cids AS uuid[]))
    AND c.embedding_model = :model
"""

_COLUMNS = """
    c.id AS chunk_id, c.document_id, c.collection_id, c.ordinal, c.text, c.token_count,
    c.page_start, c.page_end, c.section_path, c.flags,
    d.title AS doc_title, d.source_uri, d.source_type, d.mime_type,
    d.version AS doc_version, d.source_modified_at, d.ingested_at
"""


async def resolve_scope(
    session: AsyncSession, principal: Principal, requested: Sequence[uuid.UUID] | None
) -> list[uuid.UUID]:
    """Collections the principal may read, optionally narrowed to ``requested``.

    Unknown and forbidden ids are dropped silently, so the caller cannot tell them apart.
    """
    if not principal.groups:
        return []
    rows = await session.scalars(
        text(
            "SELECT DISTINCT collection_id FROM collection_acl "
            "WHERE group_name = ANY(CAST(:groups AS text[])) ORDER BY collection_id"
        ),
        {"groups": sorted(principal.groups)},
    )
    readable = list(rows.all())
    if requested is None:
        return readable
    wanted = set(requested)
    return [c for c in readable if c in wanted]


def scope_params(principal: Principal, cids: Sequence[uuid.UUID], model: str) -> dict[str, object]:
    return {"groups": sorted(principal.groups), "cids": list(cids), "model": model}


def vector_query() -> TextClause:
    # Filters live inside the ANN scan (with pgvector iterative scan) so k rows survive them.
    # No ", id" tie-break in the inner ORDER BY: it would stop Postgres using the HNSW index.
    # f-string only interpolates module-level SQL constants, never user input.
    return text(
        f"""
        WITH v AS MATERIALIZED (
            SELECT c.id, c.embedding <=> CAST(:q AS vector) AS dist
            FROM chunks c
            WHERE {SCOPE_WHERE}
            ORDER BY c.embedding <=> CAST(:q AS vector)
            LIMIT :k)
        SELECT {_COLUMNS}, v.dist AS dist, CAST(NULL AS float) AS ts_rank
        FROM v JOIN chunks c ON c.id = v.id JOIN documents d ON d.id = c.document_id
        ORDER BY v.dist + 0, c.id
        """  # noqa: S608
    )


def keyword_query() -> TextClause:
    return text(
        f"""
        WITH q AS (SELECT websearch_to_tsquery('english', :qtext) AS tsq)
        SELECT {_COLUMNS}, CAST(NULL AS float) AS dist, ts_rank_cd(c.tsv, q.tsq) AS ts_rank
        FROM chunks c JOIN documents d ON d.id = c.document_id, q
        WHERE {SCOPE_WHERE} AND c.tsv @@ q.tsq
        ORDER BY ts_rank DESC, c.id
        LIMIT :k
        """  # noqa: S608
    )


def keyword_precheck_query() -> TextClause:
    return text(
        "SELECT numnode(tsq) AS n, CAST(querytree(tsq) AS text) AS indexable "
        "FROM (SELECT websearch_to_tsquery('english', :qtext) AS tsq) s"
    )


def chunk_access_query() -> TextClause:
    """One chunk (and its document's file location), only if the principal may read it.

    Keyed by BOTH chunk and document id so a mismatched pair is indistinguishable from a
    missing or forbidden one. The document ACL is checked as a second, independent condition.
    """
    return text(
        f"""
        SELECT c.id AS chunk_id, c.document_id, c.collection_id, c.text, c.page_start, c.page_end,
               c.section_path, c.flags, d.title AS doc_title, d.mime_type, d.storage_path,
               d.checksum_sha256, d.source_modified_at, d.source_uri
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE {SCOPE_WHERE}
          AND c.id = :chunk_id AND c.document_id = :document_id
          AND d.status = 'ready' AND d.acl_groups && CAST(:groups AS text[])
        LIMIT 1
        """  # noqa: S608
    )


async def fetch_authorized_chunk(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    embedding_model: str,
) -> Any | None:
    """Row for the chunk if readable by ``principal``, else None (same for every failure)."""
    cids = await resolve_scope(session, principal, None)
    if not cids:
        return None
    params = {
        **scope_params(principal, cids, embedding_model),
        "chunk_id": chunk_id,
        "document_id": document_id,
    }
    return (await session.execute(chunk_access_query(), params)).first()
