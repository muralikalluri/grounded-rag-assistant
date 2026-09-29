# Plan-time checks of the security-relevant shape of the deployment. Uses mock providers, so it
# needs NO Azure credentials. Run: terraform test
mock_provider "azurerm" {
  # The real provider validates these as UUIDs even at plan time.
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "11111111-1111-4111-8111-111111111111"
      object_id       = "22222222-2222-4222-8222-222222222222"
      client_id       = "33333333-3333-4333-8333-333333333333"
      subscription_id = "44444444-4444-4444-8444-444444444444"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      principal_id = "55555555-5555-4555-8555-555555555555"
      client_id    = "66666666-6666-4666-8666-666666666666"
    }
  }
}
mock_provider "random" {}
mock_provider "time" {}

variables {
  api_image          = "registry.example.com/rag-backend:test"
  worker_image       = "registry.example.com/rag-backend:test"
  web_image          = "registry.example.com/rag-web:test"
  oidc_issuer        = "https://sso.example.com/realms/northwind"
  oidc_jwks_url      = "https://sso.example.com/realms/northwind/protocol/openid-connect/certs"
  oidc_client_secret = "test-client-secret"
  llm_api_key        = "test-llm-key"
}

run "only_web_is_exposed_to_the_internet" {
  command = plan

  assert {
    condition     = azurerm_container_app.web.ingress[0].external_enabled == true && azurerm_container_app.web.ingress[0].allow_insecure_connections == false
    error_message = "web must be the one external app, HTTPS only."
  }
  assert {
    condition     = azurerm_container_app.api.ingress[0].external_enabled == false
    error_message = "api must not be exposed to the internet."
  }
  assert {
    condition     = azurerm_container_app.redis.ingress[0].external_enabled == false
    error_message = "redis must be internal."
  }
  assert {
    condition     = length(azurerm_container_app.worker.ingress) == 0
    error_message = "worker must have no ingress at all."
  }
}

run "database_is_private_and_pgvector_is_allowlisted" {
  command = plan

  assert {
    condition     = azurerm_postgresql_flexible_server.main.public_network_access_enabled == false
    error_message = "Postgres must have no public endpoint."
  }
  assert {
    condition     = azurerm_postgresql_flexible_server_configuration.extensions.name == "azure.extensions" && azurerm_postgresql_flexible_server_configuration.extensions.value == "VECTOR"
    error_message = "azure.extensions must allow-list VECTOR or CREATE EXTENSION vector fails."
  }
  assert {
    condition     = azurerm_postgresql_flexible_server_configuration.require_ssl.value == "on"
    error_message = "TLS to Postgres must be required."
  }
  assert {
    condition     = azurerm_postgresql_flexible_server.main.geo_redundant_backup_enabled == false && azurerm_postgresql_flexible_server.main.backup_retention_days == 7
    error_message = "Backups: 7 days, LRS, as documented for the PoC."
  }
}

run "storage_is_locked_down" {
  command = plan

  assert {
    condition     = azurerm_storage_account.main.min_tls_version == "TLS1_2" && azurerm_storage_account.main.https_traffic_only_enabled == true
    error_message = "Storage must require TLS 1.2 over HTTPS."
  }
  assert {
    condition     = azurerm_storage_account.main.allow_nested_items_to_be_public == false
    error_message = "No public blob access."
  }
  assert {
    condition     = azurerm_storage_account.main.network_rules[0].default_action == "Deny"
    error_message = "Storage firewall must default to deny."
  }
}

run "no_secret_is_ever_a_plain_environment_value" {
  command = plan

  assert {
    condition = alltrue([
      for app in [azurerm_container_app.api, azurerm_container_app.worker] :
      length([
        for e in app.template[0].container[0].env : e.name
        if contains(["DATABASE_URL", "REDIS_URL", "AUDIT_HMAC_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"], e.name) && e.value != null
      ]) == 0
    ])
    error_message = "api/worker secrets must be secret references, never plain values."
  }
  assert {
    condition = length([
      for e in azurerm_container_app.web.template[0].container[0].env : e.name
      if contains(["OIDC_CLIENT_SECRET", "SESSION_SECRET", "REDIS_URL"], e.name) && e.value != null
    ]) == 0
    error_message = "web secrets must be secret references, never plain values."
  }
  assert {
    condition = alltrue([
      for app in [azurerm_container_app.api, azurerm_container_app.worker, azurerm_container_app.web, azurerm_container_app.redis] :
      length([for sec in app.secret : sec.name if sec.value != null]) == 0
    ]) && length([for sec in azurerm_container_app_job.migrate.secret : sec.name if sec.value != null]) == 0
    error_message = "No container-app secret may carry an inline value: every secret must be a Key Vault reference."
  }
}

run "apps_run_with_production_settings" {
  command = plan

  assert {
    condition = alltrue([
      for app in [azurerm_container_app.api, azurerm_container_app.worker] :
      lookup({ for e in app.template[0].container[0].env : e.name => e.value }, "APP_ENV", "") == "prod"
      && lookup({ for e in app.template[0].container[0].env : e.name => e.value }, "DEV_AUTH_ENABLED", "") == "false"
      && lookup({ for e in app.template[0].container[0].env : e.name => e.value }, "CRAWL_ALLOW_PRIVATE_NETWORKS", "") == "false"
      && lookup({ for e in app.template[0].container[0].env : e.name => e.value }, "EXPOSE_STAGE1_SCORES", "") == "false"
    ])
    error_message = "Backend must run with APP_ENV=prod, no dev auth, strict crawler, no score exposure."
  }
  assert {
    condition     = lookup({ for e in azurerm_container_app.api.template[0].container[0].env : e.name => e.value }, "LLM_PROVIDER", "") != "extractive"
    error_message = "The offline test mode must never be deployed."
  }
  assert {
    condition     = lookup({ for e in azurerm_container_app.api.template[0].container[0].env : e.name => e.value }, "OIDC_ISSUER", "") == var.oidc_issuer
    error_message = "API must validate tokens against the configured issuer."
  }
}

run "singleton_services_and_the_migrate_job_cannot_run_twice" {
  command = plan

  assert {
    condition     = azurerm_container_app.redis.template[0].min_replicas == 1 && azurerm_container_app.redis.template[0].max_replicas == 1
    error_message = "Redis must be exactly one replica (two would be two databases)."
  }
  assert {
    condition     = azurerm_container_app.api.template[0].max_replicas == 1 && azurerm_container_app.worker.template[0].max_replicas == 1
    error_message = "api and worker start as single replicas."
  }
  assert {
    condition     = azurerm_container_app_job.migrate.manual_trigger_config[0].parallelism == 1 && azurerm_container_app_job.migrate.manual_trigger_config[0].replica_completion_count == 1
    error_message = "Alembic takes no lock: the migrate job must be manual with parallelism 1."
  }
  assert {
    condition     = azurerm_container_app_job.migrate.replica_retry_limit == 0
    error_message = "Do not auto-retry a failed migration."
  }
}

run "identity_can_only_read_secrets_and_the_model_cache_is_not_on_smb" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.apps_read_secrets.role_definition_name == "Key Vault Secrets User"
    error_message = "The apps' identity may only READ secrets."
  }
  assert {
    condition     = azurerm_key_vault.main.rbac_authorization_enabled == true
    error_message = "Key Vault must use RBAC."
  }
  assert {
    condition = alltrue([
      for v in azurerm_container_app.api.template[0].volume : v.storage_type == "EmptyDir" if v.name == "hf"
    ])
    error_message = "The Hugging Face cache must be ephemeral, not on Azure Files."
  }
}

# ----------------------------------------------------------- input validation must bite
run "the_offline_test_provider_cannot_be_selected" {
  command = plan
  variables {
    llm_provider = "extractive"
  }
  expect_failures = [var.llm_provider]
}

run "http_issuers_are_rejected" {
  command = plan
  variables {
    oidc_issuer = "http://sso.example.com/realms/northwind"
  }
  expect_failures = [var.oidc_issuer]
}

run "bad_name_prefixes_are_rejected" {
  command = plan
  variables {
    name_prefix = "Bad_Prefix"
  }
  expect_failures = [var.name_prefix]
}
