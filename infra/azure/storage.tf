# Azure Files (SMB), not Blob: the app does filesystem I/O on STORAGE_DIR. Blob would need a
# storage abstraction in the code (SPEC §14 lists S3/Blob under "Later").
#trivy:ignore:AZU-0057 PoC: no Log Analytics workspace is provisioned to receive storage logs.
#trivy:ignore:AZU-0058 PoC: LRS is deliberate (cost); the share only caches uploads that can be re-ingested.
#trivy:ignore:AZU-0061 PoC: infrastructure (double) encryption is not enabled; platform-managed encryption at rest still applies.
resource "azurerm_storage_account" "main" {
  name                     = "st${local.compact}"
  location                 = azurerm_resource_group.main.location
  resource_group_name      = azurerm_resource_group.main.name
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  tags                     = local.tags

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  # Container Apps can only mount Azure Files with the account key, so shared-key access must
  # stay enabled. Compensating controls: the firewall below, no SAS tokens are issued, the key
  # exists only in Terraform state and the environment's storage definition, and the README
  # ("Rotating secrets") describes how to rotate the key.
  shared_access_key_enabled = true

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
    virtual_network_subnet_ids = [azurerm_subnet.apps.id]
    ip_rules                   = []
  }

  share_properties {
    retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_share" "files" {
  name               = "documents"
  storage_account_id = azurerm_storage_account.main.id # control-plane create: works behind the firewall
  quota              = var.files_quota_gb
}
