# Same state storage as 01-azure, separate key = separate state.
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "sttfstatech4821"
    container_name       = "tfstate"
    key                  = "02-databricks.tfstate"
    use_azuread_auth     = true
  }
}
