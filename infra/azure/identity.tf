# One user-assigned identity shared by all apps. Created BEFORE the apps so its Key Vault
# permission exists when the platform resolves secret references.
resource "azurerm_user_assigned_identity" "apps" {
  name                = "id-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

#trivy:ignore:AZU-0013 The firewall is Deny whenever key_vault_allowed_ips is set (Trivy cannot evaluate the conditional).
#trivy:ignore:AZU-0016 PoC: purge protection off so destroy + recreate works; documented in README.
resource "azurerm_key_vault" "main" {
  name                = "kv-${local.compact}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"
  tags                = local.tags

  # RBAC, not access policies.
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  # PoC: off, so destroy + recreate works. Turn ON for anything real (see README "Destroying").
  purge_protection_enabled = false

  # With allowed IPs the firewall denies everyone else; trusted Azure services (Container Apps
  # resolving secret references) and the apps subnet are still allowed.
  network_acls {
    default_action             = length(var.key_vault_allowed_ips) > 0 ? "Deny" : "Allow"
    bypass                     = "AzureServices"
    ip_rules                   = var.key_vault_allowed_ips
    virtual_network_subnet_ids = [azurerm_subnet.apps.id]
  }
}

# The apps' identity may READ secrets. That is its only role anywhere.
resource "azurerm_role_assignment" "apps_read_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

# Whoever runs terraform must be able to WRITE the secrets it generates.
resource "azurerm_role_assignment" "deployer_write_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Role assignments take a while to propagate; without this the first apply can fail to write
# secrets or the apps can fail to resolve them ("re-run apply" is the documented fallback).
resource "time_sleep" "rbac_propagation" {
  create_duration = "60s"
  depends_on = [
    azurerm_role_assignment.apps_read_secrets,
    azurerm_role_assignment.deployer_write_secrets,
  ]
}
