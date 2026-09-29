"""Single place that writes ACLs, so document/chunk copies cannot drift.

Invariants: chunk.acl_groups == document.acl_groups, and
document.acl_groups == collection ACL ∩ document.acl_hint (hint NULL = no narrowing).
An empty collection ACL means nobody can read (``&&`` never matches an empty array).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Collection, CollectionAcl


def effective_acl(collection_groups: Iterable[str], hint: Iterable[str] | None) -> list[str]:
    groups = set(collection_groups)
    if hint is not None:
        groups &= set(hint)
    return sorted(groups)


async def get_collection_acl(session: AsyncSession, collection_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(CollectionAcl.group_name).where(CollectionAcl.collection_id == collection_id)
    )
    return sorted(rows.all())


async def set_collection_acl(
    session: AsyncSession, collection_id: uuid.UUID, groups: Iterable[str]
) -> None:
    """Replace a collection's ACL and propagate it to documents and chunks.

    Runs in the caller's transaction: all-or-nothing. No re-embedding needed.
    """
    new_groups = sorted(set(groups))
    # Serialise with in-flight ingests (they hold FOR SHARE on this row).
    locked = await session.scalar(
        select(Collection.id).where(Collection.id == collection_id).with_for_update()
    )
    if locked is None:
        raise LookupError("collection not found")
    await session.execute(delete(CollectionAcl).where(CollectionAcl.collection_id == collection_id))
    session.add_all([CollectionAcl(collection_id=collection_id, group_name=g) for g in new_groups])
    await session.flush()
    await session.execute(
        text(
            """
            UPDATE documents SET acl_groups = ARRAY(
                SELECT g FROM unnest(CAST(:groups AS text[])) AS g
                WHERE documents.acl_hint IS NULL OR g = ANY(documents.acl_hint)
                ORDER BY g)
            WHERE collection_id = :cid
            """
        ),
        {"groups": new_groups, "cid": collection_id},
    )
    await session.execute(
        text(
            """
            UPDATE chunks SET acl_groups = d.acl_groups
            FROM documents d
            WHERE chunks.document_id = d.id AND d.collection_id = :cid
            """
        ),
        {"cid": collection_id},
    )
