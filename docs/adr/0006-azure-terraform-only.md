# 6. Terraform for Azure only, Container Apps

Status: accepted (MVP)

**Decision.** Ship `infra/azure` (Container Apps, Postgres Flexible Server, Key Vault, Azure Files); AWS is
deferred. Migrations run as a manual Container App Job.

**Consequences.** Validated, unit-tested with mock providers and scanned in CI, but never applied to a real
subscription. Azure Files forces shared-key access; Postgres is private-only, so it is reachable only from the
apps. Entra ID is not supported by the token verifier yet, so a Keycloak realm must be supplied.
