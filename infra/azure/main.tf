data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  suffix = random_string.suffix.result
  base   = "${var.name_prefix}-${var.environment}-${local.suffix}"
  # Storage account / Key Vault names: 3-24 chars, no dashes for storage.
  compact = "${var.name_prefix}${var.environment}${local.suffix}"

  tags = merge(
    {
      project      = "grounded-rag-assistant"
      environment  = var.environment
      "managed-by" = "terraform"
    },
    var.tags,
  )

  web_name    = "${var.name_prefix}-web-${local.suffix}"
  api_name    = "${var.name_prefix}-api-${local.suffix}"
  worker_name = "${var.name_prefix}-worker-${local.suffix}"
  redis_name  = "${var.name_prefix}-redis-${local.suffix}"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.base}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_management_lock" "rg" {
  count      = var.enable_locks ? 1 : 0
  name       = "protect-${local.base}"
  scope      = azurerm_resource_group.main.id
  lock_level = "CanNotDelete"
  notes      = "Remove (enable_locks = false, apply) before terraform destroy."
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.log_daily_cap_gb
  tags                = local.tags
}
