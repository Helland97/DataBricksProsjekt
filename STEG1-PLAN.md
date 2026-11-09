# Steg 1: Infrastruktur som kode (Terraform)

## 0. Why Terraform at all

Terraform lets you write your infrastructure as code. You describe what should exist, and Terraform works out what to create, change or delete to get there.
- **It's reproducible.** The same code gives the same environment, so dev and prod can be built from the same files.
- **It's reviewable.** Changes go through pull requests and you read `terraform plan` before anything happens.
- **It can be torn down.** `terraform destroy` removes everything it created, which is how you avoid surprise costs.
- **It keeps state.** Terraform keeps a state file mapping your code to real resource IDs. That file is why remote state matters (section 2).

You'll use two **providers**, which are plugins that talk to an API:
- `azurerm` talks to Azure Resource Manager and creates Azure resources: the resource group, storage and the workspace.
- `databricks` talks to the Databricks workspace API and configures what's inside it: Unity Catalog, warehouses, jobs and Git.

## 1. Install the tools

```powershell
winget install Hashicorp.Terraform
winget install Microsoft.AzureCLI
```
Open a new terminal, then run:
```powershell
terraform -version
az login
az account show   # confirm the right subscription ("Free Trial"), note the subscription id
```

Both providers sign in through your Azure CLI login, so you don't need any secrets in the code. In prod you'd use a service principal or managed identity instead (section 9).

## 2. Remote state (bootstrap)

**Why:** The state file is the source of truth for what Terraform manages.
- If it only lives on your laptop, losing the laptop means losing it. Teammates or CI can't use it, and two people running `apply` at once can corrupt it.
- It can also contain secrets. Your GitHub token will end up in it in section 5.
- Remote state in Azure Blob Storage gives you shared access, **locking** (via blob leases) and versioning.

**The chicken-and-egg problem:** Terraform can't store its state in a storage account that the same Terraform run creates. So you create the state storage once by hand, outside Terraform:

```powershell
az group create -n rg-tfstate -l westeurope
az storage account create -n sttfstate<yourinitials><random> -g rg-tfstate -l westeurope --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false
az storage container create -n tfstate --account-name sttfstate<...> --auth-mode login
```
- The storage account name must be globally unique, lowercase, and 3–24 characters.
- Storing state costs fractions of a cent.
- `terraform destroy` won't delete this resource group. Delete it manually at the very end with `az group delete -n rg-tfstate`.

**Region:** choose one region and use it everywhere. Serverless SQL and serverless jobs aren't available in every region. `westeurope` and `northeurope` are safe choices. Check the docs before picking `norwayeast`.

## 3. Folder layout: split provisioning and configuration

All of this goes inside the `infra/` folder of your existing `DataBricksProsjekt` repo:

```
infra/
  bootstrap/README.md        # the az commands above, documented
  01-azure/                  # azurerm: RG, ADLS Gen2, workspace, access connector
    providers.tf  backend.tf  variables.tf  main.tf  outputs.tf
    terraform.example.tfvars
  02-databricks/             # databricks provider: UC, external location, warehouse, job, Git
    providers.tf  backend.tf  variables.tf  main.tf  outputs.tf
    terraform.example.tfvars
```

**Why two separate root modules (two states):**
- **Technical reason:** the `databricks` provider needs the workspace URL to connect. That URL doesn't exist until `01-azure` has been applied. Configuring a provider from a resource created in the same run is unreliable: plans fail, and `destroy` can break.
- **Lifecycle:** the infrastructure (networking, storage, workspace) rarely changes. The configuration (schemas, jobs, grants) changes often. Splitting them means a mistake in a job definition can't touch your storage account. This limits the *blast radius*.
- **Ownership:** in real teams, platform/cloud teams usually own provisioning, while data/platform engineers own the workspace configuration.

`02-databricks` reads outputs from `01-azure` through `terraform_remote_state`, which is also a good demo of remote state.

Your `.gitignore` already ignores `*.tfvars` but allows `*.example.tfvars`. **Commit `.terraform.lock.hcl`**, which pins provider versions so CI uses the same ones.

## 4. `01-azure`: provisioning with azurerm

**backend.tf**
```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "sttfstate<...>"
    container_name       = "tfstate"
    key                  = "dev/01-azure.tfstate"
  }
}
```

**providers.tf:** pin `azurerm` to `~> 4.0`. Version 4 *requires* `subscription_id` in the provider block. Put it in a variable and leave it out of git.

**main.tf: the resources and why each exists**

| Resource | Key settings | Why |
|---|---|---|
| `azurerm_resource_group` | name like `rg-dbxprosjekt-dev` | One container for everything, so cost tracking and cleanup are simple. |
| `azurerm_storage_account` | `account_kind = "StorageV2"`, `account_tier = "Standard"`, `account_replication_type = "LRS"`, **`is_hns_enabled = true`** | `is_hns_enabled` (hierarchical namespace) is what makes the account **ADLS Gen2**: real directories, atomic renames and POSIX-style ACLs. Delta/Spark need these to be efficient. LRS is the cheapest. |
| `azurerm_storage_container` | e.g. `lake` (in v4 it uses `storage_account_id`) | The filesystem where your lakehouse data lives. |
| `azurerm_databricks_workspace` | **`sku = "trial"`**, `managed_resource_group_name = "rg-dbxprosjekt-dev-managed"` | `trial` is Premium with 14 days of free DBUs. Unity Catalog needs Premium. Azure creates the managed resource group for the VMs and disks Databricks uses. |
| `azurerm_databricks_access_connector` | `identity { type = "SystemAssigned" }` | A **managed identity** that Databricks uses to reach your storage, so there are no keys or secrets to rotate. |
| `azurerm_role_assignment` | role `Storage Blob Data Contributor`, scope = storage account, principal = the connector's `identity[0].principal_id` | Gives that identity read/write access to the lake, following least privilege: only this one account. |

**outputs.tf:** `workspace_url` (prefix it with `https://`), `workspace_id`, `access_connector_id`, `storage_account_name` and `container_name`.

**Run it:**
```powershell
cd infra/01-azure
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```
Read the plan every time. Get used to checking for anything marked "destroy" or "replace" before you apply.

**Check:** open the workspace from the portal. Under **Catalog**, a metastore should already be attached. New Azure workspaces are enabled for Unity Catalog automatically, with one metastore per region.

## 5. `02-databricks`: configuration with the databricks provider

**providers.tf**
```hcl
data "terraform_remote_state" "azure" {
  backend = "azurerm"
  config = { resource_group_name = "rg-tfstate", storage_account_name = "sttfstate<...>",
             container_name = "tfstate", key = "dev/01-azure.tfstate" }
}
provider "databricks" {
  host      = data.terraform_remote_state.azure.outputs.workspace_url
  auth_type = "azure-cli"
}
```
Use a separate state key here, such as `dev/02-databricks.tfstate`.

**Unity Catalog chain.** Build it in this order, because each step depends on the one before:

1. **`databricks_storage_credential`** with an `azure_managed_identity { access_connector_id = ... }` block. This registers the access connector inside Unity Catalog, so UC can use that identity.
2. **`databricks_external_location`**: set `url = "abfss://lake@<account>.dfs.core.windows.net/"` and `credential_name` to the credential above. This tells UC that this storage path may be accessed with that credential. It's what lets UC govern files in your own storage.
3. **`databricks_catalog`**: e.g. `name = "regnskap"`, `storage_root = "abfss://lake@<account>.dfs.core.windows.net/catalog"`, and `force_destroy = true` (dev only).
   - Automatically created metastores often have no default storage location, so the catalog needs its own `storage_root`. Managed tables are stored there.
   - `force_destroy` lets `destroy` delete the catalog even when it has content.
4. **`databricks_schema`**: use `for_each = toset(["bronze", "silver", "gold"])` with `catalog_name` set to the catalog. You get one schema per medallion layer, and later (step 4) you can grant access per layer.

**SQL warehouse: `databricks_sql_endpoint`**
- `cluster_size = "2X-Small"`, `max_num_clusters = 1`, `enable_serverless_compute = true`, `auto_stop_mins = 10` or lower.
- **Why serverless:** it starts in seconds. More importantly, it runs in Databricks' compute rather than VMs in your subscription. That means it **doesn't use your free trial's vCPU quota**, which is capped at about 4 cores.
- **Cost:** a warehouse costs money while it runs. Auto-stop is your protection.

**Git credential and Git folder**
- First create a **fine-grained personal access token** on GitHub: Settings → Developer settings → Fine-grained tokens. Limit it to *only* `DataBricksProsjekt`, give it **Contents: Read and write**, and set a short expiry.
- `databricks_git_credential`: `git_provider = "gitHub"`, `git_username = "Helland97"`, `personal_access_token = var.github_pat`.
- Declare `github_pat` with `sensitive = true`. Pass it as an environment variable, never in a file:
  ```powershell
  $env:TF_VAR_github_pat = "<token>"
  ```
- `databricks_repo`: `url = "https://github.com/Helland97/DataBricksProsjekt.git"`, `path = "/Workspace/Users/<your-email>/DataBricksProsjekt"`, `branch = "main"`.
- **Why "where possible":**
  - Git credentials are **per user**, so the credential belongs to whoever runs Terraform.
  - Databricks recommends the **Databricks GitHub App** for linking GitHub, and that can only be set up in the UI, not in Terraform.
  - The PAT ends up **in the state file**. That's another reason state must be remote and access-controlled.

**Job: `databricks_job`.** Make a minimal placeholder now; the real one comes in Steg 5.
- Use a `git_source` block: `url` = the repo, `provider = "gitHub"`, `branch = "main"`.
  - **This is the "Git as source" principle:** the job fetches code from the branch at run time. Nothing gets copied into the workspace.
- Add one task with `notebook_task { notebook_path = "orchestration/hello", source = "GIT" }`.
  - Commit a tiny `orchestration/hello.py` first. Its first line must be `# Databricks notebook source`, then something like `print("hello")`.
- Don't define a cluster. A task with no cluster spec runs on **serverless jobs compute**, which again avoids the vCPU quota.
- Add no schedule yet, or one with `pause_status = "PAUSED"`.

**Run it:** the same `init → fmt → validate → plan → apply` as before.

## 6. Verify in the UI

- **Catalog:** `regnskap` should be there with `bronze`, `silver` and `gold`. External Locations should show yours, and **Test connection** should be green.
- **SQL Warehouses:** the warehouse exists and is stopped. Start it, run `SELECT 1`, and confirm it auto-stops afterwards.
- **Workspace → Users → you:** the `DataBricksProsjekt` Git folder is there, on `main`.
- **Jobs:** **Run now** on the job should succeed and show the commit hash it ran.

Then commit everything: `.tf` files, `.terraform.lock.hcl` and the example tfvars, but no real tfvars and no state.

## 7. Cleanup order (remember this)

```powershell
cd infra/02-databricks; terraform destroy
cd ../01-azure;         terraform destroy
az group delete -n rg-tfstate     # only when the whole project is finished
```
Destroy in reverse order. `02` needs the workspace to still exist so it can delete things inside it.

## 8. Free-trial gotchas

- If something asks you to **"Upgrade" to pay-as-you-go, stop**. That removes the spending limit.
- The 14 free DBU days start when the workspace is created. Run `destroy` on `01-azure` during long breaks; recreating it takes about 10 minutes.
- **Role assignment errors:** you need the Owner role on the subscription, which a free account owner normally has.
- **Catalog creation fails with "no storage root":** you left out `storage_root` (section 5, item 3).

## 9. "Be ready to explain" — talking points

**Remote state:**
- State maps code to real resources.
- Remote state gives shared access, locking against simultaneous `apply`, versioning and recovery, and a single source of truth for CI.
- It's sensitive (it contains secrets), so restrict access and keep it in its own resource group or subscription.
- Bootstrapping it outside Terraform is a deliberate, common pattern.

**Separating provisioning from configuration:**
- Provider dependency: the workspace URL must exist before the databricks provider can connect.
- Different rate of change and ownership.
- Smaller blast radius and faster plans.
- You can rebuild the configuration without touching the data storage.

**What changes from dev to prod:**
- **Isolation:** separate subscriptions or at least separate workspaces and state files per environment, using the same modules with different `tfvars`.
- **Auth:** a service principal, with OIDC from CI, instead of your personal `az login`. Jobs `run_as` a service principal, and the GitHub App replaces a personal PAT.
- **Networking:**
  - VNet injection and private endpoints for storage and the workspace, and no public IP for clusters.
  - Disable public network access.
- **Data safety:**
  - Storage replication GRS/ZRS instead of LRS, soft delete and versioning on storage.
  - `prevent_destroy` lifecycle rules, and `force_destroy = false` on catalogs.
- **Process:** `apply` only from CI after an approved PR, plus drift detection and policy checks (tflint, checkov, Azure Policy).
- **Code promotion:** prod jobs run from a release branch or tag rather than `main`.
- **Sizing and cost:** larger warehouses with autoscaling, budgets per environment, and resource tags for cost allocation.
