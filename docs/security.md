# Security notes and threat model

Scope: the local compose stack and the Azure Terraform. The demo users and passwords in
`infra/keycloak` are fictional and public; **never** use that realm outside a throwaway local stack.

| Threat | Control | Evidence |
|---|---|---|
| A user reads a document they should not (via search, answer, citation or page image) | ACL predicate lives in the SQL of every retrieval path; denied chunk and missing chunk are indistinguishable; admin role grants no read bypass; fail closed if groups are unknown | `tests/test_retrieval_acl.py`, `tests/test_preview_api.py`, eval "chunks returned that the user may not read" |
| Prompt injection inside a document | Sources are delimited untrusted data; output is JSON only; the validator drops anything not backed by a verbatim quote, so injected text cannot add claims | `tests/test_grounding.py`, `tests/test_prompt_and_gate.py` |
| Model fabricates a fact or citation | Alias sources, verbatim-quote check, numbers/negations must be inside the quote, two-stage abstention | validator tests |
| Forged or replayed token | RS256 pinned, `iss`/`aud`/`azp`/`typ` checked, JWKS cached with backoff; ID and refresh tokens rejected | `tests/test_oidc.py` |
| Token theft from the browser | Tokens never reach JavaScript; opaque httpOnly session cookie, state/nonce/PKCE, SameSite=Lax + Origin + `X-Requested-With` CSRF checks | `frontend` tests |
| SSRF through the crawler | Every resolved IP must be public, IP pinned with Host/SNI, redirects re-validated, size/time/request budgets | `tests/test_crawler.py` |
| Secrets in logs | Only chunk ids are logged, never text; JWTs redacted; LiteLLM loggers are pinned to WARNING (not covered by a test) | `tests/test_chat_service.py` (no question text in logs), `tests/test_oidc.py` (tokens never logged), `tests/test_llm_provider.py` |
| Public database / secrets in config | Azure Postgres has public access off; secrets in Key Vault; `terraform test` asserts it | `infra/azure/tests`, `tests/test_deploy_config.py` |
| Dev shortcuts in production | `APP_ENV=prod` refuses dev auth, the offline `extractive` provider and private-network crawling | `tests/test_extractive.py`, `tests/test_oidc.py`, `tests/test_crawler.py` |

## Known gaps (not mitigated in v0.1.0)

- No per-user rate limiting, no audit-log UI (each chat writes one structured log line with ids, counts and an HMAC of the question, never text), and no OpenTelemetry.
- Group changes apply at the next access token (up to the token lifetime).
- Entra ID / Cognito are not supported by the verifier yet.
- The Azure Terraform has never been applied to a real subscription.
- Azure Files needs shared-key access (a Container Apps limitation); see `infra/azure/README.md`.
