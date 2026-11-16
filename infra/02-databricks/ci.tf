# --- CI identity: Unity Catalog grants ----------------------------------------
#
# The CI service principal (Entra app gh-dbxprosjekt-ci, STEG5-PLAN.md section 2) is workspace admin through
# Contributor on the workspace resource. Workspace admin gives no Unity Catalog privileges, so it gets the
# minimum it needs here:
#   - terraform plan must be able to read (refresh) every UC object in this module
#   - dbt builds on PRs read bronze and create their own pr_<n>_* schemas
#
# databricks_grant (singular) only manages THIS principal's privileges on each object. databricks_grants
# (plural) is authoritative and would remove every other grant on the object.

locals {
  # Every schema needs USE_SCHEMA, otherwise terraform plan can't read it. Only bronze gets more:
  #   SELECT       dbt PR builds read the bronze sources
  #   READ_VOLUME  terraform plan reads the two bronze volumes (landing, dlt_staging)
  # silver, gold and audit get no SELECT: PR builds write their own pr_<n>_* schemas and never read prod.
  # The keys are the schema names, so the existing "bronze" grant keeps its address in state.
  ci_schema_grants = {
    for s in var.schemas : s => {
      schema     = databricks_schema.medallion[s].id
      privileges = s == "bronze" ? ["USE_SCHEMA", "SELECT", "READ_VOLUME"] : ["USE_SCHEMA"]
    }
  }

  ci_grants = merge(local.ci_schema_grants, {
    # There is no metadata-only privilege on a storage credential, and refresh needs at least one privilege.
    # READ_FILES is the weakest, but it does let CI read raw files in the lake. Fine for synthetic data.
    # The same goes for the external location: BROWSE is not enough to read it ("non-BROWSE privileges").
    credential = { storage_credential = databricks_storage_credential.lake.name, privileges = ["READ_FILES"] }
    location   = { external_location = databricks_external_location.lake.name, privileges = ["READ_FILES"] }
    catalog    = { catalog = databricks_catalog.this.name, privileges = ["USE_CATALOG", "BROWSE", "CREATE_SCHEMA"] }
  })
}

resource "databricks_grant" "ci" {
  # No CI principal configured = no grants.
  for_each = { for k, v in local.ci_grants : k => v if var.ci_principal != null }

  storage_credential = try(each.value.storage_credential, null)
  external_location  = try(each.value.external_location, null)
  catalog            = try(each.value.catalog, null)
  schema             = try(each.value.schema, null)

  principal  = var.ci_principal
  privileges = each.value.privileges
}
