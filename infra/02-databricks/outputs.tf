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

output "git_folder_path" {
  value = databricks_repo.project.path
}

output "dbt_job_url" {
  value = databricks_job.dbt.url
}
