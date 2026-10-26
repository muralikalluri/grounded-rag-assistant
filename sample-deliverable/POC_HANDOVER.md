# Proof-of-concept handover: grounded document assistant

_Sample of what a Starter engagement delivers. Northwind Logistics is a fictional company and every
document, person and figure in the demo corpus is invented._

## 1. What was built

A question-answering assistant over a private document set that:

- answers **only** from the documents, quoting the exact passage for every sentence;
- says "not found" instead of guessing;
- shows each user only what their group may read (HR sees HR files, Engineering sees engineering files);
- opens the cited PDF page with the quoted text highlighted.

| Piece | Choice |
|---|---|
| Sources | File upload (PDF, DOCX, HTML, text) and a polite, SSRF-guarded web crawl |
| Search | Postgres + pgvector + full-text, fused with RRF, local cross-encoder rerank |
| Access control | Keycloak single sign-on; groups become an ACL filter **inside the SQL query** |
| Answering | OpenAI or Anthropic via LiteLLM; a validator checks every quote against the source |
| UI | Next.js chat with a citation panel |
| Deployment | `docker compose` locally; Terraform for Azure (Container Apps, Postgres, Key Vault) |

## 2. Results

Everything between the markers is generated from the newest report in `eval/reports/` by `make docs`.

<!-- results:start -->
_Generated from [`eval/reports/2026-10-26_7c1117f.json`](eval/reports/2026-10-26_7c1117f.json) by `make docs`; do not edit by hand._

Run `20261026T074347Z-370aa5`, mode `llm`, 30 questions (20 answerable / 6 unanswerable / 4 acl-denied), git `7c1117f`, embeddings `BAAI/bge-small-en-v1.5`, reranker `BAAI/bge-reranker-base`.

Environment: Python 3.12.14, torch 2.14.0, device `mps`.

| Metric | Result |
|---|---|
| Retrieval recall@6, file level | 19/20 (0.95) [95% CI 0.76–0.99] |
| Retrieval recall@6, page level (PDF) | 11/12 (0.92) [95% CI 0.65–0.99] |
| Chunks returned that the asking user may not read | **0** of 180 |
| Forbidden documents retrieved (ACL-denied questions) | **0** |
| Answer correctness (macro fact fraction) | 0.95 over 20 answers |
| False abstention | 0/20 (0.00) [95% CI 0.00–0.16] |
| Abstention accuracy, unanswerable + ACL-denied | 10/10 (1.00) [95% CI 0.72–1.00] |
| Citation precision | 20/20 (1.00) [95% CI 0.84–1.00] |
| Latency p50 / p95 | 1,316 ms / 2,392 ms |

| SPEC §9.4 target | Goal | Result | Status |
|---|---|---|---|
| Retrieval recall@6 (file level) | >= 0.90 | 19/20 (0.95) [95% CI 0.76–0.99] | not demonstrable at this n |
| Abstention accuracy (unanswerable + ACL-denied) | >= 0.95 | 10/10 (1.00) [95% CI 0.72–1.00] | not demonstrable at this n |
| False abstention rate | <= 0.10 | 0/20 (0.00) [95% CI 0.00–0.16] | not demonstrable at this n |
| Citation precision | >= 0.90 | 20/20 (1.00) [95% CI 0.84–1.00] | not demonstrable at this n |

Status uses the 95% Wilson interval, not the point estimate: at 30 questions most targets cannot be demonstrated either way.
<!-- results:end -->

Report files under `eval/reports/` include the per-question table so every number can be traced.

## 3. How to run it

```bash
cp .env.example .env        # placeholders only; add ANTHROPIC_API_KEY or OPENAI_API_KEY to enable answers
make up                     # Postgres, Redis, Keycloak, API, worker, web
make ingest-demo            # index the 100 fictional demo documents
```

Open http://localhost:3000 and sign in as `alice` (`alice-demo-only`) or `bob` (`bob-demo-only`).
Ask both "What is the salary band for L5 engineers?": Alice gets a cited answer, Bob gets "not found".
These accounts exist only in the throwaway local Keycloak.

- Tests: `make test` · Lint and types: `make lint` · Eval: `make eval EVAL_ARGS=--yes` (needs a key)

## 4. What is not proven yet

- **Answer quality with a live model** is unmeasured until the eval is run with your key.
- **The Azure deployment** passes validation, unit tests and a security scan but was never applied.
- Only 30 evaluation questions exist, so confidence intervals are wide.
- Entra ID sign-in, additional connectors (Drive, SharePoint, databases) and dashboards are not built.

## 5. Suggested next steps for a real engagement

1. Point the pipeline at a sample of your real documents and write 60+ questions with your domain experts.
2. Run the eval with your chosen model, tune the abstention threshold on a dev split, keep a hold-out.
3. Map your identity provider's groups to collections (Entra ID support needs a small verifier change).
4. Apply the Terraform in a sandbox subscription, then add monitoring and a retention policy.
5. Decide on rate limits, audit-log retention and who administers collections.
