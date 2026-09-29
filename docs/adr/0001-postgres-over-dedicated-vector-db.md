# 1. Postgres with pgvector instead of a dedicated vector database

Status: accepted

**Context.** Retrieval must filter by access control *inside* the search, and combine vector, keyword
and metadata conditions.

**Decision.** Keep chunks, embeddings (HNSW), full-text search and ACL arrays in one Postgres table.

**Consequences.** ACL and search are one SQL statement, so there is no post-filtering step that could
leak; one system to operate and back up. Scale ceiling is lower than a dedicated store; HNSW with
filters needs iterative scans (pgvector 0.8), which is covered by a test that forces the index.
