# --- Outputs from 01-azure ---------------------------------------------------

data "terraform_remote_state" "azure" {
  backend = "azurerm"

  config = {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "sttfstatech4821"
    container_name       = "tfstate"
    key                  = "01-azure.tfstate"
    use_azuread_auth     = true
  }
}

locals {
  azure = data.terraform_remote_state.azure.outputs
}

data "databricks_current_user" "me" {}

# --- Unity Catalog: storage credential, external location, catalog -----------

resource "databricks_storage_credential" "lake" {
  name    = "sc-${local.azure.storage_account_name}"
  comment = "Access connector from 01-azure"

  azure_managed_identity {
    access_connector_id = local.azure.access_connector_id
  }
}

resource "databricks_external_location" "lake" {
  name            = "el-${local.azure.lake_container_name}"
  url             = local.azure.lake_abfss_url
  credential_name = databricks_storage_credential.lake.name
  comment         = "ADLS Gen2 container from 01-azure"
}

resource "databricks_catalog" "this" {
  name         = var.catalog_name
  storage_root = "${local.azure.lake_abfss_url}${var.catalog_name}"
  comment      = "DataBricksProsjekt lakehouse"

  depends_on = [databricks_external_location.lake]
}

resource "databricks_schema" "medallion" {
  for_each = var.schemas

  catalog_name = databricks_catalog.this.name
  name         = each.key
  comment      = "${each.key} layer"
}

# --- Volumes for ingestion ---------------------------------------------------

# Managed volumes live under the catalog's storage root in the lake, so they need no
# extra credentials and are removed together with the schema.
#   landing:     raw JSONL files uploaded by hand, read by the Auto Loader comparison notebook
#   dlt_staging: dlt uploads its load files here (PUT) and then runs COPY INTO. Without it,
#                dlt would create `_dlt_staging_load_volume` by itself, outside Terraform.
locals {
  bronze_volumes = {
    landing     = "Raw files for the Auto Loader comparison (step 2, section 9)"
    dlt_staging = "Temporary load files for dlt (destination.databricks.staging_volume_name)"
  }
}

resource "databricks_volume" "bronze" {
  for_each = local.bronze_volumes

  name         = each.key
  catalog_name = databricks_catalog.this.name
  schema_name  = databricks_schema.medallion["bronze"].name
  volume_type  = "MANAGED"
  comment      = each.value
}

# --- SQL warehouse -----------------------------------------------------------

resource "databricks_sql_endpoint" "this" {
  name                      = "wh-dbxprosjekt"
  cluster_size              = var.warehouse_size
  min_num_clusters          = 1
  max_num_clusters          = 1
  auto_stop_mins            = var.warehouse_auto_stop_mins
  warehouse_type            = "PRO"
  enable_serverless_compute = true
}

# --- Git ---------------------------------------------------------------------

# Only needed for private repositories.
resource "databricks_git_credential" "github" {
  count = nonsensitive(var.github_token != null) ? 1 : 0

  git_provider          = "gitHub"
  git_username          = var.github_username
  personal_access_token = var.github_token
}

# Git folder in the workspace, for interactive work against the repo.
resource "databricks_repo" "project" {
  url    = var.git_repo_url
  path   = "${data.databricks_current_user.me.home}/DataBricksProsjekt"
  branch = var.git_branch

  depends_on = [databricks_git_credential.github]
}

# --- Job ---------------------------------------------------------------------

# Runs dbt straight from Git on the SQL warehouse. Code is never copied into
# the workspace; every run checks out var.git_branch.
resource "databricks_job" "dbt" {
  name = "dbt-build"

  git_source {
    url      = var.git_repo_url
    provider = "gitHub"
    branch   = var.git_branch
  }

  environment {
    environment_key = "dbt"

    spec {
      client       = "2"
      dependencies = ["dbt-databricks>=1.9,<2.0"]
    }
  }

  task {
    task_key        = "dbt_build"
    environment_key = "dbt"

    dbt_task {
      source            = "GIT"
      project_directory = "dbt"
      commands          = ["dbt deps", "dbt build"]
      warehouse_id      = databricks_sql_endpoint.this.id
      catalog           = databricks_catalog.this.name
      schema            = "silver"
    }
  }

  schedule {
    quartz_cron_expression = "0 0 6 * * ?"
    timezone_id            = "Europe/Oslo"
    pause_status           = var.job_schedule_paused ? "PAUSED" : "UNPAUSED"
  }

  depends_on = [databricks_git_credential.github]
}
