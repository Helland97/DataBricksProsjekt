# These outputs are read by ../02-databricks through terraform_remote_state.

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "location" {
  value = azurerm_resource_group.this.location
}

output "workspace_url" {
  description = "Full workspace URL, https://adb-<id>.<n>.azuredatabricks.net"
  value       = "https://${azurerm_databricks_workspace.this.workspace_url}"
}

output "workspace_resource_id" {
  description = "Azure resource id of the workspace."
  value       = azurerm_databricks_workspace.this.id
}

output "workspace_id" {
  description = "Numeric Databricks workspace id."
  value       = azurerm_databricks_workspace.this.workspace_id
}

output "access_connector_id" {
  description = "Azure resource id of the access connector (Unity Catalog storage credential)."
  value       = azurerm_databricks_access_connector.this.id
}

output "storage_account_name" {
  value = azurerm_storage_account.lake.name
}

output "lake_container_name" {
  value = azurerm_storage_container.lake.name
}

output "lake_abfss_url" {
  description = "Root URL of the lake container, used as Unity Catalog external location."
  value       = "abfss://${azurerm_storage_container.lake.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}
