# State storage is created once by hand, see ../bootstrap/README.md.
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "sttfstatech4821"
    container_name       = "tfstate"
    key                  = "01-azure.tfstate"
    use_azuread_auth     = true
  }
}
