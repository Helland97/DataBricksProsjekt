terraform {
  required_version = ">= 1.9"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
  }
}

# The workspace only exists after 01-azure has been applied, which is why this
# configuration lives in its own root module and reads it from remote state.
# Auth comes from the environment: `az login` locally, ARM_* variables in CI.
provider "databricks" {
  host                        = data.terraform_remote_state.azure.outputs.workspace_url
  azure_workspace_resource_id = data.terraform_remote_state.azure.outputs.workspace_resource_id
}
