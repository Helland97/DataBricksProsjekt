variable "catalog_name" {
  description = "Unity Catalog catalog for the project."
  type        = string
  default     = "lakehouse_dev"
}

variable "schemas" {
  description = "Medallion schemas created in the catalog."
  type        = set(string)
  default     = ["bronze", "silver", "gold"]
}

variable "warehouse_size" {
  description = "SQL warehouse t-shirt size."
  type        = string
  default     = "2X-Small"
}

variable "warehouse_auto_stop_mins" {
  description = "Minutes of inactivity before the SQL warehouse stops."
  type        = number
  default     = 10
}

variable "git_repo_url" {
  description = "Git repository with the project code (dbt, ingestion, ...)."
  type        = string
  default     = "https://github.com/Helland97/DataBricksProsjekt"
}

variable "git_branch" {
  description = "Branch that the Git folder and job check out."
  type        = string
  default     = "main"
}

variable "github_username" {
  description = "GitHub user for the Git credential. Only used when github_token is set."
  type        = string
  default     = "Helland97"
}

variable "github_token" {
  description = "GitHub PAT. Only needed if the repository is private; leave null for a public repo."
  type        = string
  default     = null
  sensitive   = true
}

variable "job_schedule_paused" {
  description = "Keep the daily dbt job schedule paused (run it manually until the pipeline is ready)."
  type        = bool
  default     = true
}
