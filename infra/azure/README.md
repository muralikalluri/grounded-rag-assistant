# Azure deployment (Terraform)

Proof-of-concept infrastructure for the whole stack on Azure. **Status: validated and unit-tested, never applied.**
No Azure subscription was available while building it, so nothing here has been deployed and no
screenshot exists. What is verified is listed under [Verification](#verification).

## What it creates

| Piece | Resource | Notes |
|---|---|---|
| Network | VNet, delegated subnets | Postgres and the Container Apps environment are reachable only inside it |
| Database | PostgreSQL Flexible Server 16 | Public access **off**, private DNS, TLS required, `azure.extensions=VECTOR` so the migration can `CREATE EXTENSION vector` |
| Secrets | Key Vault (RBAC) | Generated passwords and your OIDC/LLM secrets; apps read them through one user-assigned identity with *Key Vault Secrets User* |
| Files | Storage account + Azure Files share | Mounted as `STORAGE_DIR` (uploads and page renders); firewall denies everything but the apps subnet |
| Compute | Container Apps: `web` (external ingress), `api` (internal), `worker` (no ingress), `redis` (internal TCP) | `redis`, `worker` and `api` are pinned to one replica; `web` may scale to two (sessions live in Redis) |
| Logs | Log Analytics workspace | Receives the Container Apps logs; retention and a daily cap are variables |
| Identity | One user-assigned managed identity | Only role: *Key Vault Secrets User* |
| Migrations | Container App **Job** `migrate` | Manual trigger, parallelism 1, no retries. Run it before first use and after every upgrade |

## Deploy

```bash
cd infra/azure
cp terraform.tfvars.example terraform.tfvars      # git-ignored; edit it
export ARM_SUBSCRIPTION_ID=...                    # or set subscription_id
export TF_VAR_oidc_client_secret=...              # secrets via environment, not the file
export TF_VAR_llm_api_key=...
az login
terraform init
terraform apply
az containerapp job start -n "$(terraform output -raw migrate_job_name)" -g "$(terraform output -raw resource_group)"
# wait until the job execution has Succeeded (Azure portal or `az containerapp job execution list`), then restart the API:
az containerapp revision restart -g "$(terraform output -raw resource_group)" -n "$(terraform output -raw api_app_name)" \
  --revision "$(az containerapp revision list -g "$(terraform output -raw resource_group)" -n "$(terraform output -raw api_app_name)" --query '[0].name' -o tsv)"
```

The API starts before the schema exists on a first deploy, so the restart is required once.

You must first build and push the images yourself (`api_image`, `worker_image`, `web_image`);
image publishing is not automated. Then register `terraform output oidc_redirect_uri` as a valid
redirect URI in your identity provider.

**Identity provider:** the API's verifier expects a Keycloak-shaped token (`groups` claim). Entra ID is
not supported yet. Point `oidc_issuer` / `oidc_jwks_url` at a Keycloak realm that is reachable from the
internet. Do not use the dev realm in `infra/keycloak` (it contains demo users with published passwords).

## Security posture and accepted findings

`terraform fmt`, `validate`, `terraform test` and a Trivy config scan run in CI. Trivy findings that are
accepted are annotated in the `.tf` files next to the resource with the reason:

- **Key Vault purge protection is off** so `destroy` followed by `apply` works while iterating. Turn it on for
  anything real.
- **Key Vault firewall** is `Deny` when `key_vault_allowed_ips` is set; otherwise it is open to the network
  (RBAC still applies). Set your IP.
- **Secrets have no expiry.** See [Rotating secrets](#rotating-secrets).
- **Storage:** shared-key access must stay enabled because Container Apps can only mount Azure Files with the
  account key. The key lives in Terraform state and the environment definition. LRS, no storage logging and no
  infrastructure encryption are PoC cost choices.

**State contains secrets** (generated passwords, the storage key). Use a remote backend with encryption and
restricted access for anything beyond a personal experiment; local state is git-ignored.

## Rotating secrets

The generated values come from `random_password` resources, so replacing only the Key Vault secret would
store the same value again. Replace the password itself, apply, then restart the affected apps:

```bash
terraform apply -replace=random_password.session      # or .audit_hmac
# postgres / redis: also changes the server or Redis password; expect a short outage
terraform apply -replace=random_password.redis
```

- Your own secrets (`oidc_client_secret`, `llm_api_key`): change the `TF_VAR_` value and apply.
- **Registry credential** (`registry_password`): change the variable and apply; the operator owns its rotation.
- **Storage account key:** `az storage account keys renew`, then `terraform apply` so the environment's storage
  definition picks up the new key, then restart the apps that mount the share. Until then the share is unmounted.
- Restart after any rotation: `az containerapp revision restart` for `api`, `worker` and `web`.

## Known gaps

- **The API connects to Postgres as the administrator login** (password auth). A least-privilege application
  role and Entra database auth are not implemented.
- **No cost estimate is included.** SPEC §10 asks for a monthly cost table; prices change and cannot be
  hand-typed here. Generate one from the plan with a tool such as infracost before quoting a figure.
- Key Vault reachability from Container Apps with the firewall on `Deny`, and Azure Files with the storage
  firewall on `Deny`, are designed from the documentation and untested (no apply).

## Destroying

`terraform destroy` removes everything, including the database and uploaded files. With
`enable_locks = true` the locks must be removed first. Key Vault is soft-deleted for 7 days; recreating
a vault with the same name inside that window fails unless you purge it (`az keyvault purge`).

## Verification

| Check | How | Result |
|---|---|---|
| Format, schema | `terraform fmt -check -recursive && terraform validate` | passes |
| Security assertions | `terraform test` (mock provider, `tests/security.tftest.hcl`) | passes; asserts no public DB, RBAC vault, internal API, no dev auth in prod, etc. |
| Config matches the app | `pytest backend/tests/test_deploy_config.py` | passes; every env var the Terraform sets is a real setting, every referenced secret exists |
| Static scan | Trivy `config` (pinned version in CI) | no findings at MEDIUM and above after the annotations above |
| **Actual apply** | needs an Azure subscription | **not done** |

Unverified assumptions: the minimum pgvector version offered by Azure Flexible Server (the schema uses HNSW,
which needs pgvector 0.5+; iterative scans need 0.8) and the cost of the default SKUs.
