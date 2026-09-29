# 2. ACL groups are denormalised onto every chunk

Status: accepted

**Decision.** `chunks.acl_groups` (plus `collection_id`, `embedding_model`) is copied from the collection
at ingest, and updated under a row lock when the collection ACL changes.

**Consequences.** Filtering needs no join and works with the vector index. Cost: an ACL change rewrites
chunk rows, and correctness depends on the lock protocol (tested with a concurrency test).
A single scoped query builder is the only code allowed to read `chunks`; static and runtime scans enforce it.
