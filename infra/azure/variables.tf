variable "subscription_id" {
  description = "Azure subscription id. Leave null to use ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "location" {
  description = "Azure region. Check that Container Apps, PostgreSQL Flexible Server (pgvector) and your SKUs are available there."
  type        = string
  default     = "westeurope"
}

variable "name_prefix" {
  description = "Short lowercase prefix for resource names (a random suffix is appended)."
  type        = string
  default     = "rag"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,8}$", var.name_prefix))
    error_message = "name_prefix must be 2-9 lowercase letters/digits, starting with a letter (storage and Key Vault names are length-limited)."
  }
}

variable "environment" {
  description = "Environment label used in names and tags."
  type        = string
  default     = "poc"
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------- container images
variable "api_image" {
  description = "Image for the API and the migrate job (the backend image), e.g. myregistry.azurecr.io/rag-backend:1.0.0. Build and push it yourself; image publishing is Later."
  type        = string
}

variable "worker_image" {
  description = "Image for the ingestion worker. The backend image with a different command is fine."
  type        = string
}

variable "web_image" {
  description = "Image for the Next.js web app."
  type        = string
}

variable "redis_image" {
  description = "Redis image. Pinned to 7.2: newer Redis releases changed license terms; use Valkey if that matters to you."
  type        = string
  default     = "docker.io/library/redis:7.2-alpine"
}

variable "registry_server" {
  description = "Private registry host (e.g. myregistry.azurecr.io). Empty = public images only."
  type        = string
  default     = ""
}

variable "registry_username" {
  description = "Registry username (only used when registry_server is set)."
  type        = string
  default     = ""
}

variable "registry_password" {
  description = "Registry password/token (only used when registry_server is set)."
  type        = string
  default     = ""
  sensitive   = true
}

# --------------------------------------------------------------------------- OIDC
variable "oidc_issuer" {
  description = "Public issuer URL of YOUR Keycloak realm, e.g. https://sso.example.com/realms/northwind. Must be reachable from the internet (browsers and Azure). See README: Entra ID is not supported yet."
  type        = string

  validation {
    condition     = startswith(var.oidc_issuer, "https://")
    error_message = "oidc_issuer must be an https:// URL."
  }
}

variable "oidc_jwks_url" {
  description = "JWKS URL of that realm, e.g. https://sso.example.com/realms/northwind/protocol/openid-connect/certs."
  type        = string

  validation {
    condition     = startswith(var.oidc_jwks_url, "https://")
    error_message = "oidc_jwks_url must be an https:// URL."
  }
}

variable "oidc_client_id" {
  description = "Confidential OIDC client used by the web app (the API requires azp to equal it)."
  type        = string
  default     = "rag-web"
}

variable "oidc_audience" {
  description = "Audience the API requires in access tokens (an audience mapper must add it)."
  type        = string
  default     = "rag-api"
}

variable "oidc_client_secret" {
  description = "Client secret of the OIDC client. Stored in Key Vault."
  type        = string
  sensitive   = true
}

variable "oidc_groups_require_leading_slash" {
  description = "true = groups must be Keycloak full paths ('/HR'); the safe setting for real realms (ACL group names must then be stored the same way). false matches the demo realm."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------- LLM
variable "llm_provider" {
  description = "Which hosted model provider the API uses."
  type        = string
  default     = "anthropic"

  validation {
    condition     = contains(["anthropic", "openai"], var.llm_provider)
    error_message = "llm_provider must be anthropic or openai (the offline 'extractive' test mode is never deployed)."
  }
}

variable "llm_model" {
  description = "Model id for the chosen provider (LiteLLM name without the provider prefix). Verify it against your account."
  type        = string
  default     = "claude-opus-5-5"
}

variable "llm_api_key" {
  description = "API key for the chosen provider. Stored in Key Vault."
  type        = string
  sensitive   = true
}

# -------------------------------------------------------------------- sizing knobs
variable "postgres_sku" {
  description = "PostgreSQL Flexible Server SKU. HNSW index builds like memory: B_Standard_B2s is the smallest sensible PoC size."
  type        = string
  default     = "B_Standard_B2s"
}

variable "postgres_storage_mb" {
  description = "Postgres storage in MB (auto-grow is on)."
  type        = number
  default     = 32768
}

variable "files_quota_gb" {
  description = "Size of the Azure Files share that holds the raw documents."
  type        = number
  default     = 50
}

variable "api_cpu" {
  description = "vCPU for the API and worker (Consumption needs memory = 2 x cpu). Unmeasured starting point; the local models need a few GB."
  type        = number
  default     = 2
}

variable "api_memory" {
  description = "Memory for the API and worker, e.g. 4Gi."
  type        = string
  default     = "4Gi"
}

variable "log_retention_days" {
  description = "Log Analytics retention."
  type        = number
  default     = 30
}

variable "log_daily_cap_gb" {
  description = "Log Analytics daily ingestion cap (cost guard)."
  type        = number
  default     = 1
}

variable "key_vault_allowed_ips" {
  description = "Public IPs (CIDR or address) allowed to reach Key Vault, e.g. the machine running terraform. Empty = Key Vault firewall left open (still RBAC-protected). When set, the Key Vault firewall denies everyone else."
  type        = list(string)
  default     = []
}

variable "enable_locks" {
  description = "Put a CanNotDelete lock on the resource group. Remove it (set false and apply) before terraform destroy."
  type        = bool
  default     = false
}
