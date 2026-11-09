variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "project" {
  description = "Short project name used in resource names."
  type        = string
  default     = "dbxprosjekt"

  validation {
    # Storage account names are max 24 chars: "st" + project + environment + "lake".
    condition     = can(regex("^[a-z0-9]{3,12}$", var.project))
    error_message = "project must be 3-12 lowercase letters or digits."
  }
}

variable "environment" {
  description = "Environment name, e.g. dev."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9]{2,6}$", var.environment))
    error_message = "environment must be 2-6 lowercase letters or digits."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "northeurope"
}

variable "lake_container_name" {
  description = "ADLS Gen2 container that holds the Unity Catalog data."
  type        = string
  default     = "lakehouse"
}

variable "tags" {
  description = "Extra tags added to every resource."
  type        = map(string)
  default     = {}
}
