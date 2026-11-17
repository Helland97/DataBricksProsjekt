# Copy to terraform.tfvars (git-ignored). All values have defaults; uncomment to override.
# The workspace URL and storage details come from 01-azure via terraform_remote_state.

# catalog_name             = "lakehouse_dev"
# warehouse_size           = "2X-Small"
# warehouse_auto_stop_mins = 10
# git_branch               = "main"
# job_schedule_paused      = true

# Only for a private repository. Prefer TF_VAR_github_token over writing it here.
# github_token = "ghp_..."

# Application (client) id of the CI service principal (STEG5-PLAN.md section 2). Leave out = no CI grants.
# ci_principal = "00000000-0000-0000-0000-000000000000"

# Who gets an email when regnskap-daily fails.
# alert_emails = ["you@example.com"]
