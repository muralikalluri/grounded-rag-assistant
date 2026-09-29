resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  version             = "16"
  sku_name            = var.postgres_sku
  storage_mb          = var.postgres_storage_mb
  tags                = local.tags

  # Private only: reachable from the VNet, never from the internet.
  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  # Password auth only for now: Entra database auth needs token-callback code changes (Later).
  # The app connects as the admin login: a least-privilege gap, documented in the README "Known gaps".
  administrator_login    = local.postgres_admin
  administrator_password = random_password.postgres.result
  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = false
  }

  # Small PoC: no HA, no geo-redundancy. Automated backups stay on.
  backup_retention_days        = 7
  geo_redundant_backup_enabled = false
  auto_grow_enabled            = true

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  lifecycle {
    ignore_changes = [zone] # Azure picks the zone; do not fight it
  }
}

resource "azurerm_postgresql_flexible_server_database" "rag" {
  name      = local.postgres_db
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

# The migration runs CREATE EXTENSION vector, which Azure only allows for allow-listed extensions.
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

# TLS is mandatory (the default); stated explicitly so a change shows up in review.
resource "azurerm_postgresql_flexible_server_configuration" "require_ssl" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

# Connection and checkpoint logging (a subset of the CIS Azure benchmark checks). Server-side only: the logs contain no
# query text or parameters, and nothing here is shipped anywhere until a workspace is attached.
resource "azurerm_postgresql_flexible_server_configuration" "logging" {
  for_each = {
    "log_connections"            = "on"
    "log_checkpoints"            = "on"
    "log_disconnections"         = "on"
    "connection_throttle.enable" = "on"
  }
  name      = each.key
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = each.value

  # Azure rejects concurrent parameter changes on one server; serialise behind the others.
  depends_on = [
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_postgresql_flexible_server_configuration.require_ssl,
  ]
}
