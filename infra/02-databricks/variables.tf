variable "catalog_name" {
  description = "Unity Catalog catalog for the project."
  type        = string
  default     = "lakehouse_dev"
}

variable "schemas" {
  description = "Schemas created in the catalog: the medallion layers, plus audit for dbt's stored test failures."
  type        = set(string)
  default     = ["bronze", "silver", "gold", "audit"]
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
  description = "Pause the daily regnskap-daily schedule. Changed through a PR, so CI's plan and the real job agree."
  type        = bool
  # The schedule's state lives here, not in terraform.tfvars: CI has no tfvars, and with a different value its
  # plan would show "pause the schedule" on every PR. Pausing or resuming = a one-line PR + terraform apply.
  default = false
}

variable "ci_principal" {
  description = "Application (client) id of the CI service principal gh-dbxprosjekt-ci (STEG5 section 2). Null = no CI grants."
  type        = string
  default     = null
}

variable "alert_emails" {
  description = "Email addresses that get a message when the job fails. Set in terraform.tfvars, not in code (public repo)."
  type        = list(string)
  default     = []
  # The plan is posted on PRs in a public repo (terraform-plan.yml): print "(sensitive value)", not the address.
  sensitive = true
}
