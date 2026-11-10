output "workspace_url" {
  value = local.azure.workspace_url
}

output "catalog_name" {
  value = databricks_catalog.this.name
}

output "schemas" {
  value = sort([for s in databricks_schema.medallion : s.name])
}

output "sql_warehouse_id" {
  value = databricks_sql_endpoint.this.id
}

output "sql_warehouse_http_path" {
  description = "HTTP path for dbt profiles / JDBC clients."
  value       = databricks_sql_endpoint.this.odbc_params[0].path
}

output "volume_paths" {
  description = "File paths of the bronze volumes, e.g. for `databricks fs cp`."
  value       = { for k, v in databricks_volume.bronze : k => v.volume_path }
}

output "dlt_staging_volume_name" {
  description = "Value for destination.databricks.staging_volume_name in ingestion/.dlt/config.toml."
  value       = "${databricks_catalog.this.name}.${databricks_schema.medallion["bronze"].name}.${databricks_volume.bronze["dlt_staging"].name}"
}

output "git_folder_path" {
  value = databricks_repo.project.path
}

output "dbt_job_url" {
  value = databricks_job.dbt.url
}
