output "web_url" {
  description = "Public URL of the web app."
  value       = local.web_url
}

output "oidc_redirect_uri" {
  description = "Register EXACTLY this redirect URI in your OIDC client (and the base URL as post-logout redirect)."
  value       = "${local.web_url}/auth/callback"
}

output "migrate_job_name" {
  description = "Run once after the first apply: az containerapp job start -n <name> -g <resource group>."
  value       = azurerm_container_app_job.migrate.name
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "api_app_name" {
  description = "Restart its revision after the migrate job finishes."
  value       = azurerm_container_app.api.name
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "postgres_fqdn" {
  description = "Private DNS name (resolvable only inside the VNet)."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}
