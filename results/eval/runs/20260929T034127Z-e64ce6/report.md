# Evaluation report `20260929T034127Z-e64ce6`

> **Generation metrics: not run (no LLM configured).** Only retrieval and ACL checks below.

> Working tree was dirty when this ran (`git`: no-commits-yet).

## Run

| | |
|---|---|
| mode | retrieval_only |
| questions | 30 (all) |
| errors (excluded from all rates) | 0 |
| git | `no-commits-yet` (dirty) |
| dataset sha256 | `eec10ca4f4086c77…` |
| corpus manifest sha256 | `cd81264b664d9b5c…` |
| embedding / reranker | `BAAI/bge-small-en-v1.5` / `BAAI/bge-reranker-base` |
| abstain_rerank_threshold | 0.19 |

## Retrieval and access control

| Metric | Value |
|---|---|
| Recall@6, file level (answerable) | 19/20 (0.95) [95% CI 0.76–0.99] |
| Recall@6, page level (PDF sources with a page anchor) | 12/13 (0.92) [95% CI 0.67–0.99] |
| Chunks returned that the asking user may not read | **0** of 180 |
| Forbidden documents retrieved (ACL-denied questions) | **0** |

### Stage-1 gate preview (top rerank probability vs. the threshold)

| Category | would abstain at stage 1 |
|---|---|
| answerable | 0/20 |
| unanswerable | 4/6 |
| acl_denied | 3/4 |

## Targets (SPEC §9.4)

| Metric | Target | Result | Status |
|---|---|---|---|
| Retrieval recall@6 (file level) | >= 0.90 | 19/20 (0.95) [95% CI 0.76–0.99] | not demonstrable at this n |
| Abstention accuracy (unanswerable + ACL-denied) | >= 0.95 | n/a | not run |
| False abstention rate | <= 0.10 | n/a | not run |
| Citation precision | >= 0.90 | n/a | not run |

Status is decided by the 95% Wilson interval, not the point estimate: with this few questions most targets cannot be demonstrated either way.

## Per question

| id | user | category | outcome | file recall | notes |
|---|---|---|---|---|---|
| q001 | alice | answerable | retrieval_only | hit / page hit |  |
| q002 | bob | answerable | retrieval_only | hit / page hit |  |
| q003 | alice | answerable | retrieval_only | hit / page hit |  |
| q004 | alice | answerable | retrieval_only | hit / page hit |  |
| q005 | bob | answerable | retrieval_only | hit / page hit |  |
| q006 | alice | answerable | retrieval_only | hit / page hit |  |
| q007 | bob | answerable | retrieval_only | hit / page hit |  |
| q008 | alice | answerable | retrieval_only | hit / page hit |  |
| q009 | bob | answerable | retrieval_only | hit |  |
| q010 | alice | answerable | retrieval_only | MISS / page MISS |  |
| q011 | bob | answerable | retrieval_only | hit |  |
| q012 | alice | answerable | retrieval_only | hit / page hit |  |
| q013 | bob | answerable | retrieval_only | hit |  |
| q014 | alice | answerable | retrieval_only | hit |  |
| q015 | bob | answerable | retrieval_only | hit |  |
| q016 | alice | answerable | retrieval_only | hit / page hit |  |
| q017 | alice | answerable | retrieval_only | hit / page hit |  |
| q018 | alice | answerable | retrieval_only | hit |  |
| q019 | bob | answerable | retrieval_only | hit |  |
| q020 | bob | answerable | retrieval_only | hit / page hit |  |
| q021 | alice | unanswerable | retrieval_only | n/a |  |
| q022 | alice | unanswerable | retrieval_only | n/a |  |
| q023 | alice | unanswerable | retrieval_only | n/a |  |
| q024 | bob | unanswerable | retrieval_only | n/a |  |
| q025 | bob | unanswerable | retrieval_only | n/a |  |
| q026 | alice | unanswerable | retrieval_only | n/a |  |
| q027 | bob | acl_denied | retrieval_only | n/a |  |
| q028 | bob | acl_denied | retrieval_only | n/a |  |
| q029 | bob | acl_denied | retrieval_only | n/a |  |
| q030 | alice | acl_denied | retrieval_only | n/a |  |

## Change since the previous report

No comparable previous report (same dataset, corpus and mode).

## Notes

- SPEC §9.1 asks for 60+ questions; the MVP cut (§14) is 30, so intervals are wide.
- Outages and other errors are excluded from every denominator (see `errors`).
- `not_found` caused by truncated/refused/invalid model output counts as a system failure, never as a correct abstention.
