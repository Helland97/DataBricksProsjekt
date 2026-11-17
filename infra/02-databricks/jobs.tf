# --- Job: regnskap-daily (STEG5-PLAN.md section 3) -----------------------------
#
# dlt -> bronze, then dbt build (silver/gold + data tests). The code comes straight from Git:
# every run checks out var.git_branch, so what's on that branch is what runs.

# This job used to be databricks_job.dbt ("dbt-build") in main.tf. `moved` tells Terraform it's the
# same object under a new address. The job is updated in place (same job id, same run history)
# instead of destroyed and recreated.
moved {
  from = databricks_job.dbt
  to   = databricks_job.daily
}

locals {
  # The same versions as ingestion/uv.lock and dbt/uv.lock: the job runs exactly what runs locally.
  ingest_dependencies = [
    "dlt[databricks,parquet]==1.32.0",
    "faker==40.52.0",
    "fastapi==0.162.3",
    "uvicorn==0.54.0",
  ]
  dbt_dependencies = [
    "dbt-core==1.12.6",
    "dbt-databricks==1.13.0",
  ]
}

resource "databricks_job" "daily" {
  name                = "regnskap-daily"
  description         = "dlt -> bronze, then dbt build (silver/gold + data tests). Code from Git, branch ${var.git_branch}."
  max_concurrent_runs = 1    # two runs at once would merge into the same tables
  timeout_seconds     = 3600 # the whole run; normally a few minutes

  queue {
    enabled = true # a manual run during the scheduled one waits instead of being skipped
  }

  git_source {
    url      = var.git_repo_url
    provider = "gitHub"
    branch   = var.git_branch
  }

  # "auto" = derive the simulated day from the run date (section 4.2). Override with
  # "Run now with different parameters" to load a specific day.
  parameter {
    name    = "sim_day"
    default = "auto"
  }

  environment {
    environment_key = "ingest"
    spec {
      environment_version = "4" # serverless environment 4 = Python 3.12, which ingestion/pyproject.toml requires
      dependencies        = local.ingest_dependencies
    }
  }

  environment {
    environment_key = "dbt"
    spec {
      environment_version = "4"
      dependencies        = local.dbt_dependencies
    }
  }

  task {
    task_key                  = "ingest"
    environment_key           = "ingest"
    max_retries               = 1
    min_retry_interval_millis = 60000
    timeout_seconds           = 1800

    spark_python_task {
      source      = "GIT"
      python_file = "ingestion/jobs/daily_load.py" # written in section 4
      parameters = [
        "--day", "{{job.parameters.sim_day}}",
        "--run-date", "{{job.start_time.iso_date}}",
        "--server-hostname", trimprefix(local.azure.workspace_url, "https://"),
        "--http-path", databricks_sql_endpoint.this.odbc_params[0].path,
      ]
    }
  }

  task {
    task_key        = "dbt_build"
    environment_key = "dbt"

    depends_on {
      task_key = "ingest"
    }

    dbt_task {
      source            = "GIT"
      project_directory = "dbt"
      commands = [
        "dbt deps",
        "dbt source freshness",
        "dbt build --exclude-resource-type unit_test",
      ]
      warehouse_id = databricks_sql_endpoint.this.id
      catalog      = databricks_catalog.this.name
      schema       = "silver"
    }
  }

  schedule {
    quartz_cron_expression = "0 0 6 * * ?"
    timezone_id            = "Europe/Oslo"
    pause_status           = var.job_schedule_paused ? "PAUSED" : "UNPAUSED"
  }

  # Only when there's an address to send to. An empty block would show up as a change on every plan.
  dynamic "email_notifications" {
    for_each = length(var.alert_emails) > 0 ? [1] : []
    content {
      on_failure                = var.alert_emails
      no_alert_for_skipped_runs = true
    }
  }

  health {
    rules {
      metric = "RUN_DURATION_SECONDS"
      op     = "GREATER_THAN"
      value  = 1800 # half an hour means something hangs
    }
  }

  depends_on = [databricks_git_credential.github]
}
