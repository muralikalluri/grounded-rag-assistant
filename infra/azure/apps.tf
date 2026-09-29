resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.base}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  tags                       = local.tags

  # Workload-profiles environment, Consumption profile. The environment itself is externally
  # reachable; which APPS are exposed is decided per app by ingress.external_enabled.
  internal_load_balancer_enabled = false
  zone_redundancy_enabled        = false

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# Shared documents volume (api reads for page previews, worker writes).
resource "azurerm_container_app_environment_storage" "files" {
  name                         = "documents"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azurerm_storage_share.files.name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

locals {
  use_registry = var.registry_server != ""

  # Fixed public URL of the web app, computed without referencing the app (avoids a cycle).
  web_url = "https://${local.web_name}.${azurerm_container_app_environment.main.default_domain}"

  llm_key_env_name = var.llm_provider == "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"
  llm_model_env    = var.llm_provider == "anthropic" ? "ANTHROPIC_MODEL" : "OPENAI_MODEL"

  # Settings shared by the API, the worker and the migrate job. APP_ENV=prod turns on the
  # app's own production validators (OIDC required, real audit key, no dev auth, no test mode).
  backend_env = {
    APP_ENV                           = "prod"
    DEV_AUTH_ENABLED                  = "false"
    LOG_LEVEL                         = "INFO"
    STORAGE_DIR                       = "/data/files"
    HF_HOME                           = "/hf-cache"
    LLM_PROVIDER                      = var.llm_provider
    OIDC_ISSUER                       = var.oidc_issuer
    OIDC_JWKS_URL                     = var.oidc_jwks_url
    OIDC_AUDIENCE                     = var.oidc_audience
    OIDC_CLIENT_ID                    = var.oidc_client_id
    OIDC_GROUPS_REQUIRE_LEADING_SLASH = tostring(var.oidc_groups_require_leading_slash)
    CRAWL_ALLOW_PRIVATE_NETWORKS      = "false" # the crawler runs inside the VNet: keep the SSRF guard strict
    EXPOSE_STAGE1_SCORES              = "false"
  }
  backend_env_llm = { (local.llm_model_env) = var.llm_model }

  # env var name => Key Vault-backed secret name. Never a plain value.
  backend_secret_env = {
    DATABASE_URL             = "database-url"
    REDIS_URL                = "redis-url-api"
    AUDIT_HMAC_KEY           = "audit-hmac-key"
    (local.llm_key_env_name) = "llm-api-key"
  }
  web_env = {
    NODE_ENV          = "production"
    API_URL           = "http://${local.api_name}"
    APP_ORIGIN        = local.web_url
    OIDC_ISSUER       = var.oidc_issuer
    OIDC_INTERNAL_URL = var.oidc_issuer # the IdP is public; there is no private address for it
    OIDC_CLIENT_ID    = var.oidc_client_id
  }
  web_secret_env = {
    OIDC_CLIENT_SECRET = "oidc-client-secret"
    SESSION_SECRET     = "session-secret"
    REDIS_URL          = "redis-url-web"
  }

  # Secret names each app may reference (must include every value used in *_secret_env).
  backend_secret_names = toset(concat(values(local.backend_secret_env), local.use_registry ? ["registry-password"] : []))
  web_secret_names     = toset(concat(values(local.web_secret_env), local.use_registry ? ["registry-password"] : []))

  kv_secret_ids = merge(
    { for k, s in azurerm_key_vault_secret.app : k => s.versionless_id },
    local.use_registry ? { "registry-password" = azurerm_key_vault_secret.registry_password[0].versionless_id } : {},
  )
}

# ------------------------------------------------------------------------------- redis
# Internal-only, single replica, password protected, no persistence. A restart signs everyone out
# and drops queued ingestion jobs: acceptable for a PoC (Azure Managed Redis is the upgrade path).
resource "azurerm_container_app" "redis" {
  name                         = local.redis_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  secret {
    name                = "redis-password"
    key_vault_secret_id = local.kv_secret_ids["redis-password"]
    identity            = azurerm_user_assigned_identity.apps.id
  }

  ingress {
    external_enabled = false
    transport        = "tcp"
    target_port      = 6379
    exposed_port     = 6379
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1 # exactly one: two Redis replicas would be two separate databases
    max_replicas = 1

    container {
      name    = "redis"
      image   = var.redis_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["/bin/sh", "-c", "exec redis-server --requirepass \"$REDIS_PASSWORD\" --save '' --appendonly no"]

      env {
        name        = "REDIS_PASSWORD"
        secret_name = "redis-password"
      }
    }
  }

  depends_on = [time_sleep.rbac_propagation]
}

# --------------------------------------------------------------------------------- api
resource "azurerm_container_app" "api" {
  name                         = local.api_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  dynamic "registry" {
    for_each = local.use_registry ? [1] : []
    content {
      server               = var.registry_server
      username             = var.registry_username
      password_secret_name = "registry-password"
    }
  }

  dynamic "secret" {
    for_each = local.backend_secret_names
    content {
      name                = secret.value
      key_vault_secret_id = local.kv_secret_ids[secret.value]
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  # Only the web app is exposed to the internet. The API is internal (still reachable from the
  # VNet, so its bearer-token check is the real control).
  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # One replica: a cold start downloads and loads ~1.3 GB of models.
    min_replicas = 1
    max_replicas = 1

    volume {
      name         = "files"
      storage_name = azurerm_container_app_environment_storage.files.name
      storage_type = "AzureFile"
    }
    volume {
      name         = "hf"
      storage_type = "EmptyDir" # model cache on ephemeral storage, NOT on SMB
    }

    container {
      name   = "api"
      image  = var.api_image
      cpu    = var.api_cpu
      memory = var.api_memory

      dynamic "env" {
        for_each = merge(local.backend_env, local.backend_env_llm)
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.backend_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      volume_mounts {
        name = "files"
        path = "/data"
      }
      volume_mounts {
        name = "hf"
        path = "/hf-cache"
      }

      # The models load before the port opens: allow ~6 minutes for the first start.
      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        initial_delay           = 30
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 10
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
      # /ready opens a database and a Redis connection on every call: keep the period long.
      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/ready"
        interval_seconds        = 30
        timeout                 = 10
        failure_count_threshold = 3
      }
    }
  }

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_postgresql_flexible_server_database.rag,
    azurerm_container_app.redis,
  ]

  lifecycle {
    precondition {
      condition = alltrue([
        for n in setunion(local.backend_secret_names, local.web_secret_names) : contains(local.key_vault_secret_names, n) || n == "registry-password"
      ])
      error_message = "An app references a secret that is not created in Key Vault (see secrets.tf)."
    }
  }
}

# ---------------------------------------------------------------------------- worker
resource "azurerm_container_app" "worker" {
  name                         = local.worker_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  dynamic "registry" {
    for_each = local.use_registry ? [1] : []
    content {
      server               = var.registry_server
      username             = var.registry_username
      password_secret_name = "registry-password"
    }
  }

  dynamic "secret" {
    for_each = local.backend_secret_names
    content {
      name                = secret.value
      key_vault_secret_id = local.kv_secret_ids[secret.value]
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  # No ingress: the worker only consumes jobs from Redis.
  template {
    min_replicas = 1 # arq's queue is a Redis sorted set; the KEDA list scaler does not apply
    max_replicas = 1

    volume {
      name         = "files"
      storage_name = azurerm_container_app_environment_storage.files.name
      storage_type = "AzureFile"
    }
    volume {
      name         = "hf"
      storage_type = "EmptyDir"
    }

    container {
      name    = "worker"
      image   = var.worker_image
      cpu     = var.api_cpu
      memory  = var.api_memory
      command = ["arq", "app.worker.WorkerSettings"]

      dynamic "env" {
        for_each = merge(local.backend_env, local.backend_env_llm)
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.backend_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      volume_mounts {
        name = "files"
        path = "/data"
      }
      volume_mounts {
        name = "hf"
        path = "/hf-cache"
      }
    }
  }

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_postgresql_flexible_server_database.rag,
    azurerm_container_app.redis,
  ]
}

# ------------------------------------------------------------------------------- web
resource "azurerm_container_app" "web" {
  name                         = local.web_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  dynamic "registry" {
    for_each = local.use_registry ? [1] : []
    content {
      server               = var.registry_server
      username             = var.registry_username
      password_secret_name = "registry-password"
    }
  }

  dynamic "secret" {
    for_each = local.web_secret_names
    content {
      name                = secret.value
      key_vault_secret_id = local.kv_secret_ids[secret.value]
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  # The only externally reachable app. HTTPS only.
  ingress {
    external_enabled           = true
    target_port                = 3000
    transport                  = "auto"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2 # sessions live in Redis, so no sticky sessions are needed

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = local.web_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.web_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/auth/error"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  depends_on = [time_sleep.rbac_propagation, azurerm_container_app.api]
}

# ------------------------------------------------------------------------ migrate job
# Alembic takes no lock: never run two at once (parallelism = 1, manual trigger only).
# Order on first deploy: apply -> start this job -> wait -> restart the api revision.
resource "azurerm_container_app_job" "migrate" {
  name                         = "${var.name_prefix}-migrate-${local.suffix}"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  dynamic "registry" {
    for_each = local.use_registry ? [1] : []
    content {
      server               = var.registry_server
      username             = var.registry_username
      password_secret_name = "registry-password"
    }
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = local.kv_secret_ids["database-url"]
    identity            = azurerm_user_assigned_identity.apps.id
  }
  secret {
    name                = "audit-hmac-key"
    key_vault_secret_id = local.kv_secret_ids["audit-hmac-key"]
    identity            = azurerm_user_assigned_identity.apps.id
  }
  dynamic "secret" {
    for_each = local.use_registry ? [1] : []
    content {
      name                = "registry-password"
      key_vault_secret_id = local.kv_secret_ids["registry-password"]
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  template {
    container {
      name    = "migrate"
      image   = var.api_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["alembic", "upgrade", "head"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name  = "APP_ENV"
        value = "prod"
      }
      # Alembic's env.py builds Settings(), which in prod requires these to be present and real:
      env {
        name  = "OIDC_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "OIDC_JWKS_URL"
        value = var.oidc_jwks_url
      }
      env {
        name        = "AUDIT_HMAC_KEY"
        secret_name = "audit-hmac-key"
      }
    }
  }

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_postgresql_flexible_server_database.rag,
  ]
}
