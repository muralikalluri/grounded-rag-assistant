# Evaluation report `20261026T074347Z-370aa5`

## Run

| | |
|---|---|
| mode | llm |
| questions | 30 (all) |
| errors (excluded from all rates) | 0 |
| git | `7c1117f` |
| dataset sha256 | `c5f8fb5052b5d357…` |
| corpus manifest sha256 | `cd81264b664d9b5c…` |
| embedding / reranker | `BAAI/bge-small-en-v1.5` / `BAAI/bge-reranker-base` |
| abstain_rerank_threshold | 0.19 |
| generator models seen | `gpt-4.1-mini` |
| judge | openai/default (same family as generator: unverified) |

## Retrieval and access control

| Metric | Value |
|---|---|
| Recall@6, file level (answerable) | 19/20 (0.95) [95% CI 0.76–0.99] |
| Recall@6, page level (PDF sources with a page anchor) | 11/12 (0.92) [95% CI 0.65–0.99] |
| Chunks returned that the asking user may not read | **0** of 180 |
| Forbidden documents retrieved (ACL-denied questions) | **0** |

### Stage-1 gate preview (top rerank probability below the threshold)

The threshold was tuned on the **dev** split only, so dev rows are in-sample; **holdout** rows are the fair read.

| Split | Category | would abstain at stage 1 |
|---|---|---|
| holdout | answerable | 0/10 |
| holdout | unanswerable | 1/3 |
| holdout | acl_denied | 1/2 |
| dev (tuning set) | answerable | 0/10 |
| dev (tuning set) | unanswerable | 3/3 |
| dev (tuning set) | acl_denied | 2/2 |

## Generation

| Metric | Value |
|---|---|
| Answer correctness (macro fact fraction, answered answerable) | 0.95 over 20 (full 19, partial 0, none 1) |
| Answers that also mention an older superseded value | 0 |
| End to end correct (all facts AND a citation from an expected source; of ALL answerable) | 19/20 (0.95) [95% CI 0.76–0.99] |
| False abstention (answerable, wrongly said not found) | 0/20 (0.00) [95% CI 0.00–0.16] — stage 1: 0, stage 2: 0 |
| Answerable not answered, for ANY reason (includes silent system failures) | 0/20 (0.00) [95% CI 0.00–0.16] |
| Abstention accuracy, unanswerable | 6/6 (1.00) [95% CI 0.61–1.00] |
| Abstention accuracy, ACL-denied | 4/4 (1.00) [95% CI 0.51–1.00] (denied facts leaked: 0) |
| Abstention accuracy, combined | 10/10 (1.00) [95% CI 0.72–1.00] |
| System failures (truncated / refused / invalid output) | answerable 0, negatives 0 |
| Citation precision (judge) | 20/20 (1.00) [95% CI 0.84–1.00] |
| Correctness rescued by the judge | 0 |
| Latency p50 / p95 (n=30; p95 is ~the 2nd slowest) | 1,316 ms / 2,392 ms |
| Cost | unknown (no priced calls) |
| Tokens in / out | 26,370 / 1,116 |

Cost counts only successful calls (calls that failed or were retried are not recorded), so it is a lower bound. Latency includes retries and time spent waiting for the shared model lock while other questions run (concurrency is in the run manifest).

## Targets (SPEC §9.4)

| Metric | Target | Result | Status |
|---|---|---|---|
| Retrieval recall@6 (file level) | >= 0.90 | 19/20 (0.95) [95% CI 0.76–0.99] | not demonstrable at this n |
| Abstention accuracy (unanswerable + ACL-denied) | >= 0.95 | 10/10 (1.00) [95% CI 0.72–1.00] | not demonstrable at this n |
| False abstention rate | <= 0.10 | 0/20 (0.00) [95% CI 0.00–0.16] | not demonstrable at this n |
| Citation precision | >= 0.90 | 20/20 (1.00) [95% CI 0.84–1.00] | not demonstrable at this n |

Status is decided by the 95% Wilson interval, not the point estimate: with this few questions most targets cannot be demonstrated either way.

## Per question

| id | user | category | outcome | file recall | notes |
|---|---|---|---|---|---|
| q001 | alice | answerable | answered | hit / page hit |  |
| q002 | bob | answerable | answered | hit / page hit |  |
| q003 | alice | answerable | answered | hit / page hit |  |
| q004 | alice | answerable | answered | hit / page hit |  |
| q005 | bob | answerable | answered | hit / page hit |  |
| q006 | alice | answerable | answered | hit / page hit |  |
| q007 | bob | answerable | answered | hit / page hit |  |
| q008 | alice | answerable | answered | hit / page hit |  |
| q009 | bob | answerable | answered | hit |  |
| q010 | alice | answerable | answered | MISS / page MISS |  |
| q011 | bob | answerable | answered | hit |  |
| q012 | alice | answerable | answered | hit / page hit |  |
| q013 | bob | answerable | answered | hit |  |
| q014 | alice | answerable | answered | hit |  |
| q015 | bob | answerable | answered | hit |  |
| q016 | alice | answerable | answered | hit |  |
| q017 | alice | answerable | answered | hit / page hit |  |
| q018 | alice | answerable | answered | hit |  |
| q019 | bob | answerable | answered | hit |  |
| q020 | bob | answerable | answered | hit / page hit |  |
| q021 | alice | unanswerable | abstained_stage1 | n/a | low_relevance |
| q022 | alice | unanswerable | abstained_stage2 | n/a | model_not_found |
| q023 | alice | unanswerable | abstained_stage1 | n/a | low_relevance |
| q024 | bob | unanswerable | abstained_stage2 | n/a | model_not_found |
| q025 | bob | unanswerable | abstained_stage1 | n/a | low_relevance |
| q026 | alice | unanswerable | abstained_stage1 | n/a | low_relevance |
| q027 | bob | acl_denied | abstained_stage1 | n/a | low_relevance |
| q028 | bob | acl_denied | abstained_stage1 | n/a | low_relevance |
| q029 | bob | acl_denied | abstained_stage1 | n/a | low_relevance |
| q030 | alice | acl_denied | abstained_stage2 | n/a | model_not_found |

## Change since the previous report

No comparable previous report (same dataset, corpus and mode).

## Notes

- SPEC §9.1 asks for 60+ questions; the MVP cut (§14) is 30, so intervals are wide.
- Outages and other errors are excluded from every denominator (see `errors`).
- `not_found` caused by truncated/refused/invalid model output counts as a system failure, never as a correct abstention.
