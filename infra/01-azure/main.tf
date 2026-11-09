locals {
  name = "${var.project}-${var.environment}"

  tags = merge({
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }, var.tags)
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# --- ADLS Gen2 ---------------------------------------------------------------

resource "azurerm_storage_account" "lake" {
  name                     = "st${var.project}${var.environment}lake"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true # hierarchical namespace = ADLS Gen2

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false # Entra ID only

  tags = local.tags
}

resource "azurerm_storage_container" "lake" {
  name                  = var.lake_container_name
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# --- Databricks --------------------------------------------------------------

resource "azurerm_databricks_workspace" "this" {
  name                        = "dbw-${local.name}"
  resource_group_name         = azurerm_resource_group.this.name
  location                    = azurerm_resource_group.this.location
  sku                         = "premium" # required for Unity Catalog
  managed_resource_group_name = "rg-${local.name}-dbw-managed"
  tags                        = local.tags
}

# Managed identity that Unity Catalog uses to reach the storage account.
resource "azurerm_databricks_access_connector" "this" {
  name                = "dbac-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_role_assignment" "connector_blob" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.this.identity[0].principal_id
}
