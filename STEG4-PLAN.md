# Steg 4: Tilgangsstyring (role engineering)

Goal: put access control on the lakehouse from step 1–3 with **Unity Catalog**. Groups, service principals and
grants are defined in **Terraform**. **Row filters** and **column masks** are applied to the tables dbt builds, so
one customer can never see another customer's data, and personal data is masked for roles that don't need it. Then
use **lineage** and **Delta time travel** as an audit trail. Along the way you should be able to explain least
privilege, isolation between customers, and how personal data (personopplysninger) is handled.

## 0. What already exists from step 1–3

- Catalog `lakehouse_dev` with schemas `bronze`, `silver`, `gold`, `audit` (`var.schemas` in
  `infra/02-databricks/variables.tf`), the serverless SQL warehouse `wh-dbxprosjekt`, and the `dbt-build` job.
- **Everything is owned by you today.** You created the catalog, the schemas and every table, so you have full access
  to everything. Nobody else has access to anything. This step adds the other roles.
- Gold tables with a `customer_id` column (these are the ones that need isolation):
  `dim_customers`, `fct_invoices`, `fct_gl_entries`, `rpt_ar_aging`, `rpt_ar_aging_by_customer`,
  `rpt_customer_summary`. `rpt_trial_balance` and `dim_accounts` have no customer dimension.
- Personal data lives in customer master data: `name`, `address`, `postal_code`, `city`, `email`, `org_no`. It is in
  `bronze.customers`, `silver.stg_erp__customers`, `silver.snp_erp__customers` and `gold.dim_customers`.
  `rpt_ar_aging` and `rpt_customer_summary` copy `customer_name` (and `city`).
- `01-azure` already outputs `workspace_id`, which is needed to assign account groups to the workspace.

**What "kunde" means in this step.** The data is one company's ERP, and its customers are `customer_id` 10001,
10002, ... The scenario is a **customer portal**: each customer may log in and see its own invoices, open items and
summary, and nothing about other customers. The same pattern works if "kunde" means an audit client with its own
ERP (several companies in one lakehouse). Then the filter column would be `company_id` instead of `customer_id`. Be
ready to say that the mechanism is the same, only the tenant key changes.

## 1. Roles

| Role (account group) | Who | Needs | Must not |
|---|---|---|---|
| `dbx_pipeline` | Service principal `sp-dbt` (the job) + you, for local dbt runs | Write to bronze/silver/gold/audit/governance | — (but no human should need it in prod) |
| `dbx_engineers` | Data engineers | Read all layers to debug, run and inspect jobs | Write gold by hand, see personal data in plain text |
| `dbx_analysts` | Controllers / analysts | Read gold, all customers | See bronze/silver, see email/address |
| `dbx_auditors` (revisor) | External/internal auditor | Read silver, gold, audit, history and lineage. Sees personal data (needs it for confirmations) | Change anything, anywhere |
| `kunde_<id>` | One group per customer (demo: `kunde_10001`, `kunde_10002`) | Their own rows in the customer portal | See other customers, see internal tables |

Rules that make this least privilege:
- **Groups get privileges, people don't.** A person joins or leaves a group. No grant ever names a user. That
  keeps the grants readable and makes "who has access to X" one question about group membership.
- **Only the pipeline writes.** Humans read. A change to gold goes through Git and the job, never through an
  `UPDATE` in a notebook. That's also what makes the time travel history trustworthy (section 8).
- **The job runs as a service principal**, not as you. Today the job runs as its creator, so it has all of your
  privileges. With `run_as`, the job can do exactly what `dbx_pipeline` can do, and the Delta history shows
  `sp-dbt` as the writer.
- **Use account groups, not workspace-local groups.** Unity Catalog grants only work with account-level
  principals, and row filters must use `is_account_group_member()`, not `is_member()` (which checks workspace-local
  groups).

## 2. Terraform: identities (account level)

Groups and service principals are account objects, so they need a second Databricks provider that talks to the
account console. Add it to `infra/02-databricks/providers.tf`:

```hcl
provider "databricks" {
  alias      = "account"
  host       = "https://accounts.azuredatabricks.net"
  account_id = var.databricks_account_id   # from the account console (top right). Not a secret
}
```
- Auth is `az login`, the same as the workspace provider. Your user must be **account admin**. On a personal tenant
  the first Entra global admin who logs in to the account console is one. **Check:** `terraform plan` can read
  `data "databricks_user" "me" { provider = databricks.account, user_name = ... }`.
- Keep everything in `02-databricks` (one new file `identity.tf`), not a new root module. The plan and destroy
  order stays simple. Say in the interview that in a real company identities come from Entra ID via **SCIM /
  automatic identity management**, not from the data platform's Terraform. Then Terraform would only *reference*
  the groups (`data "databricks_group"`).

**New file `infra/02-databricks/identity.tf` (sketch):**
```hcl
locals {
  role_groups     = toset(["dbx_pipeline", "dbx_engineers", "dbx_analysts", "dbx_auditors"])
  customer_groups = toset([for id in var.portal_customers : "kunde_${id}"])   # var.portal_customers = [10001, 10002]

  # One test service principal per role, so access can be tested without extra human accounts (section 7).
  test_principals = merge(
    { for g in ["dbx_engineers", "dbx_analysts", "dbx_auditors"] : "sp-test-${trimprefix(g, "dbx_")}" => g },
    { for g in local.customer_groups : "sp-test-${g}" => g },
  )
}

resource "databricks_group" "this" {
  provider     = databricks.account
  for_each     = setunion(local.role_groups, local.customer_groups)
  display_name = each.key
}

resource "databricks_service_principal" "dbt" {
  provider     = databricks.account
  display_name = "sp-dbt"
}

resource "databricks_service_principal" "test" {
  provider     = databricks.account
  for_each     = local.test_principals
  display_name = each.key
}

# Members: sp-dbt and you in dbx_pipeline, you in dbx_engineers, each test SP in its group.
resource "databricks_group_member" "..." { provider = databricks.account ... }

# Account groups must be assigned to the workspace before they can log in or use the warehouse.
resource "databricks_mws_permission_assignment" "groups" {
  provider     = databricks.account
  for_each     = databricks_group.this
  workspace_id = local.azure.workspace_id
  principal_id = each.value.id
  permissions  = ["USER"]
}

# OAuth M2M secrets for the test SPs. They end up in Terraform state (which is in Azure Storage with
# Entra auth only). Fine for a demo. Otherwise create them with the CLI and keep them out of state.
resource "databricks_service_principal_secret" "test" {
  provider             = databricks.account
  for_each             = databricks_service_principal.test
  service_principal_id = each.value.id
}
```
- On Azure, `databricks_service_principal` without `application_id` creates a **Databricks-managed** SP, which needs
  nothing in Entra ID. **Check** that your provider version supports that on Azure. If not, create the SPs in Entra
  (`azuread_application`) and pass `application_id`.
- Add a sensitive output `test_principal_credentials` (client id + secret per SP) for the test script.
- Demo customers: `10001` and `10002` (both exist from day 1). Adding a customer to the portal is a one-line
  change in `var.portal_customers` plus a row in the mapping seed (section 4). That's reviewed in a PR.

**Check:** `terraform plan` shows only additions: 4 + 2 groups, 6 SPs, members, assignments, secrets. After apply,
the groups appear under the workspace's *Identity and access* page as "account" groups.

## 3. Terraform: grants and permissions

**Grant matrix.** This is the core of the step. Write it in the README too.

| Securable | `dbx_pipeline` | `dbx_engineers` | `dbx_analysts` | `dbx_auditors` | `kunde_<id>` |
|---|---|---|---|---|---|
| catalog `lakehouse_dev` | `USE CATALOG`, `BROWSE` | `USE CATALOG`, `BROWSE` | `USE CATALOG` | `USE CATALOG`, `BROWSE` | `USE CATALOG` |
| schema `bronze` | `ALL PRIVILEGES` | `USE SCHEMA`, `SELECT`, `READ VOLUME` | — | — | — |
| schema `silver` | `ALL PRIVILEGES` | `USE SCHEMA`, `SELECT` | — | `USE SCHEMA`, `SELECT` | — |
| schema `gold` | `ALL PRIVILEGES` | `USE SCHEMA`, `SELECT` | `USE SCHEMA`, `SELECT` | `USE SCHEMA`, `SELECT` | — |
| schema `audit` | `ALL PRIVILEGES` | `USE SCHEMA`, `SELECT` | — | `USE SCHEMA`, `SELECT` | — |
| schema `governance` (new) | `ALL PRIVILEGES` | `USE SCHEMA` | — | `USE SCHEMA`, `SELECT` | — |
| schema `kundeportal` (new) | `ALL PRIVILEGES` | `USE SCHEMA`, `SELECT` | — | — | `USE SCHEMA`, `SELECT` |
| warehouse `wh-dbxprosjekt` | `CAN_USE` | `CAN_USE` | `CAN_USE` | `CAN_USE` | `CAN_USE` |
| job `dbt-build` | (runs as `sp-dbt`) | `CAN_MANAGE_RUN` | — | `CAN_VIEW` | — |
| `system.access` (audit, lineage) | — | `USE SCHEMA`, `SELECT` | — | `USE SCHEMA`, `SELECT` | — |

Notes on the matrix:
- **Grant on schemas, never on single tables.** Privileges inherit down to every table and view in the schema,
  including tables dbt creates tomorrow. Table-level grants in Terraform would fail when the table doesn't exist
  yet, and they're at the mercy of dbt's `create or replace`. That's why the customer portal is its own schema
  (section 4) instead of a few grants on gold tables.
- `BROWSE` lets engineers and auditors see that an object exists, its metadata and its lineage, without reading
  data. Auditors can follow lineage back to bronze without having `SELECT` on bronze.
- Auditors have no `MODIFY`, `CREATE` or `MANAGE` anywhere. Time travel only needs `SELECT`.
- Analysts never see silver. If they need something that isn't in gold, the answer is a new gold model, not a
  grant on silver.
- Engineers read bronze and silver, but personal data is masked for them (section 6). They debug on structure and
  keys, not on email addresses.
- Add `"governance"` and `"kundeportal"` to `var.schemas`. Change the schema comment from `"${each.key} layer"` to
  a map of comments, since the new ones aren't layers.

**Authoritative grants.** Use `databricks_grants` (plural), one per securable. It is **authoritative**: on every
`apply` it removes any grant that isn't in the code. A grant someone clicked in by hand shows up as drift in
`terraform plan` and is removed. That's a control in itself: the code *is* the access register. (The singular
`databricks_grant` is additive and doesn't do this. Know the difference.)

```hcl
# infra/02-databricks/grants.tf (sketch)
locals {
  schema_grants = {
    bronze = { dbx_pipeline = ["ALL_PRIVILEGES"], dbx_engineers = ["USE_SCHEMA", "SELECT", "READ_VOLUME"] }
    gold   = { dbx_pipeline = ["ALL_PRIVILEGES"], dbx_engineers = ["USE_SCHEMA", "SELECT"],
               dbx_analysts = ["USE_SCHEMA", "SELECT"], dbx_auditors = ["USE_SCHEMA", "SELECT"] }
    # ...
  }
}

resource "databricks_grants" "schema" {
  for_each = local.schema_grants
  schema   = databricks_schema.medallion[each.key].id
  dynamic "grant" {
    for_each = each.value
    content {
      principal  = grant.key
      privileges = grant.value
    }
  }
  depends_on = [databricks_mws_permission_assignment.groups]
}
```
- Workspace objects use `databricks_permissions`: `sql_endpoint_id` (CAN_USE) and `job_id` (CAN_MANAGE_RUN /
  CAN_VIEW). These are not Unity Catalog grants. Be able to explain the two layers: **workspace permissions**
  (who can use compute, jobs and notebooks) and **UC grants** (who can read which data).
- `system.access` must be enabled: add `databricks_system_schema` for `access` (it may already be on; then
  `terraform import` it). Grants on `system` are given by a metastore admin. Do them in Terraform as well if your
  user is metastore admin. Otherwise once by hand, and note it in the README.

**Job changes in `main.tf`:**
- `run_as { service_principal_name = databricks_service_principal.dbt.application_id }`.
- `sp-dbt` needs `CAN_USE` on the warehouse, and `dbx_pipeline` privileges on the schemas. The repo is public, so no
  Git credential is needed for the SP.

**Ownership.** Today you own every table. When the job starts running as `sp-dbt`, it has to `create or replace`
tables you own. **Set the owner of the catalog and schemas to the group `dbx_pipeline`** (`owner` on
`databricks_catalog` / `databricks_schema`). Then add `+post-hook: "alter table {{ this }} owner to dbx_pipeline"`
(or `alter view` for views) in `dbt_project.yml`, so every table is owned by the group no matter who built it.
**Check:** run the job once after a local build. If it fails with a permission error on replace, this is why.

## 4. Governance objects (built by dbt)

Terraform owns *who* (groups) and *what they can reach* (grants). dbt owns *the tables*, so it also owns the rules
that are attached to the tables: the filter functions, the mapping, the masks and the portal views. Be ready to
defend that split. The rules have to be applied every time dbt rebuilds a table, so they must live in the same
code that builds it.

**Mapping: who may see which customer.** `dbt/seeds/governance/customer_access.csv`:
```csv
group_name,customer_id,granted_by,granted_at,reason
kunde_10001,10001,engineer@example.no,2026-11-16,Kundeportal pilot
kunde_10002,10002,engineer@example.no,2026-11-16,Kundeportal pilot
```
- Seed into schema `governance` (`+schema: governance` for `seeds/governance/`). Only `dbx_pipeline` can write it.
  **Whoever can change this table can give access**, so it must be as protected as the grants. As a seed, every
  change is a reviewed commit in Git: who added which customer, when, and why.
- Why a mapping table and not `is_account_group_member(concat('kunde_', customer_id))`? The table supports one
  group seeing several customers (a group of companies, konsern, or an accountant with several clients), and access
  can be listed with one `select`. The naming convention version works too, but it hides the rules in a string.
- Add a dbt test: every `group_name` matches `^kunde_\d+$`, and every `customer_id` exists in `stg_erp__customers`.

**Functions.** Macro `dbt/macros/governance/create_governance_functions.sql`, run as a `post-hook` on the seed. Then
the functions are always created after the mapping table exists, and they show up in the DAG:
```sql
create or replace function {{ target.catalog }}.governance.rf_customer(p_customer_id bigint)
returns boolean
comment 'Row filter: internal roles see all customers. A customer group sees only its own rows.'
return
     is_account_group_member('dbx_pipeline')
  or is_account_group_member('dbx_engineers')
  or is_account_group_member('dbx_analysts')
  or is_account_group_member('dbx_auditors')
  or exists (
       select 1 from {{ target.catalog }}.governance.customer_access a
       where a.customer_id = p_customer_id
         and is_account_group_member(a.group_name)
     );

create or replace function {{ target.catalog }}.governance.mask_email(p_email string)
returns string
return case
  when is_account_group_member('dbx_pipeline') or is_account_group_member('dbx_auditors') then p_email
  when p_email is null then null
  else regexp_replace(p_email, '^[^@]+', '***')       -- ***@firma.no: domain kept for analysis
end;

create or replace function {{ target.catalog }}.governance.mask_pii(p_value string)
returns string
return case
  when is_account_group_member('dbx_pipeline') or is_account_group_member('dbx_auditors') then p_value
  else '***'
end;
```
- **Default deny.** The filter lists who may see. Everything else is false. A new group that nobody thought of sees
  nothing, which is the right failure mode.
- A customer row with `customer_id is null` (GL lines without a customer) is visible only to internal roles,
  because the `exists` never matches null.
- Masks return `'***'`, not `null`, so `not_null` tests and joins don't change behaviour for masked roles, and the
  reader can see that a value exists but is hidden.
- **Checks:** (1) a principal querying a filtered table does **not** need `EXECUTE` on the function or `SELECT` on
  `customer_access`. The function runs with the owner's rights. Verify it with a customer SP in section 7, and if it
  turns out to be wrong, grant `EXECUTE` to `kunde_*` and note it. (2) The filter is evaluated for the **querying**
  user even when the table is read through a view.

**Customer portal: `dbt/models/kundeportal/`.** Thin views over gold, schema `kundeportal`, with only the columns a
customer should see:

| View | From | Columns |
|---|---|---|
| `kp_invoices` | `fct_invoices` | invoice_id, customer_id, invoice_date, due_date, amounts, currency, kid, status |
| `kp_open_items` | `rpt_ar_aging` | invoice_id, customer_id, due_date, open_amount, days_past_due, aging_bucket |
| `kp_customer_summary` | `rpt_customer_summary` | customer_id, invoice_count, invoiced/paid/open/overdue amounts |

- Internal columns (`is_amount_parsed_from_text`, `customer_sk`, `status_updated_at`, `loaded_at`) are left out.
  Column selection is also access control, and the cheapest kind.
- The views have no `where` clause of their own. The row filter on the gold tables does the isolation. As
  **defence in depth**, add `where governance.rf_customer(customer_id)` to each view anyway. If someone one day
  removes the filter from a gold table, the portal is still isolated. (That's the older **dynamic view** pattern.
  Know both, section 9.)
- Exposure `kundeportal` in `_kundeportal__exposures.yml`, so the portal shows up at the end of the lineage.

## 5. Row filters

Apply `governance.rf_customer` on `customer_id` to: `dim_customers`, `fct_invoices`, `rpt_ar_aging`,
`rpt_ar_aging_by_customer`, `rpt_customer_summary`. Not on `fct_gl_entries` (customers have no path to it; see
the merge note below) or the tables without a customer dimension.

**How dbt applies it.** Check the `dbt-databricks` changelog for native support (a `row_filter` model config and
`column_mask` in column config, in newer 1.x versions). The difference matters:

| | Native config | `post-hook: alter table {{ this }} set row filter ...` |
|---|---|---|
| When it's applied | In the same `create` statement | After `create or replace` has finished |
| Gap | None | A few seconds where the new table has **no filter** |
| Works on any adapter version | No | Yes |

Use native config if your adapter version has it, and bump the version pin in `pyproject.toml` **and** in the
job's `dependencies` if needed. Otherwise use the post-hook, and be ready to explain the gap and why it's acceptable
here (no customer has a grant on gold, only on the portal views, so the gap is never reachable for them). That
gap is one more reason the portal is a separate schema.

```yaml
# _finance__models.yml (native config, sketch: check the exact syntax for your adapter version)
- name: fct_invoices
  config:
    row_filter:
      function: governance.rf_customer
      columns: [customer_id]
```
- Add `-- depends_on: {{ ref('customer_access') }}` to the filtered models, so dbt builds the seed (and with it the
  functions) first.
- **The pipeline must be exempt.** Row filters apply to *everyone*, including the table owner. If `dbx_pipeline`
  weren't in the filter, dbt tests like `assert_ar_subledger_reconciles_to_gl` and the incremental
  `select max(...) from {{ this }}` would see a filtered table, and they would be silently wrong. Show it once:
  remove `dbx_pipeline` from the function, run `dbt build`, see the reconciliation test fail, and put it back.
- **Incremental models and `merge`:** check the current limitations for `MERGE` into tables with row filters or
  masks before you put one on `fct_gl_entries`. This plan leaves it unfiltered and ungranted to customers.
- **Compute:** row filters and masks work on SQL warehouses and serverless. Classic dedicated clusters need a recent
  runtime that sends the filtering to serverless. Check the requirements if someone asks about all-purpose clusters.

**Check:** as `sp-test-kunde-10001`, `select distinct customer_id from kundeportal.kp_invoices` returns only `10001`,
and `select * from gold.fct_invoices` fails with `PERMISSION_DENIED`. As `sp-test-analysts`, the same gold query
returns every customer.

## 6. Column masks and PII tags

**What is personal data here?** Customers are companies, but a sole proprietorship (enkeltpersonforetak) *is* a
person. Its name, address and email are personal data. Its org.nr is public in Brønnøysundregistrene. Treat the
customer register as personal data and be ready to explain why.

| Column | Classification | Mask for engineers/analysts | Why |
|---|---|---|---|
| `email` | PII, contact | `mask_email`: `***@firma.no` | The domain is useful (which company), the local part identifies a person |
| `address` | PII, location | `mask_pii`: `***` | Not needed for any analysis here |
| `postal_code`, `city` | Low risk | not masked | Needed for regional analysis. Data minimisation is a trade-off, not "mask everything" |
| `customer_name` | PII for sole proprietorships | not masked | Analysts need to know which customer. Write the reasoning in the README |
| `org_no` | Public | not masked | Public register. For a sole proprietorship it doesn't reveal the national ID number |

**Where the masks go.** Every table where the column exists: `silver.stg_erp__customers`,
`silver.snp_erp__customers`, `gold.dim_customers`. Use native `column_mask` config, or a post-hook
`alter table {{ this }} alter column email set mask governance.mask_email`. For the snapshot, the post-hook runs
after every snapshot run, which is fine. For `bronze.customers` (built by dlt, not dbt), run the `alter` once from
a script, and add a check that it's still there after a `--refresh`, because a replace drops it.

**The pipeline must be exempt here too.** If dbt ran as a masked role, `dim_customers` would be built from `***`
values, and the damage would be permanent in the snapshot. That's why `dbx_pipeline` and not `dbx_engineers` is in
the mask's "clear" list, and why you are in `dbx_pipeline` for local runs. Write that down as a dev-only compromise:
in prod, no human is in `dbx_pipeline`.

**Tags as the PII register.** Tag the columns in dbt (`databricks_tags` in column config if the adapter supports
it, otherwise `alter table ... alter column email set tags ('pii' = 'contact')`):
```sql
select catalog_name, schema_name, table_name, column_name, tag_name, tag_value
from system.information_schema.column_tags
where tag_name = 'pii';
```
That query *is* the list of where personal data lives, which you need for the record of processing (protokoll,
GDPR article 30) and for answering an access or erasure request. Then compare it with the column lineage from
section 8: every column derived from a `pii` column should also be tagged. A dbt test or a small script can check
that.

**Next level (talk about it, don't build it):** **ABAC** (attribute-based access control) in Unity Catalog. You
define one policy, "mask every column tagged `pii=contact` for everyone except `dbx_auditors`", at catalog or
schema level, and it applies to new tables automatically. That moves the rule from every table to the tag. Check
the feature's status in your workspace. It's the answer to "how does this scale to 500 tables?".

**Check:** as `sp-test-analysts`, `select email, address from gold.dim_customers` returns `***@...` and `***`. As
`sp-test-auditors`, the clear text. As `sp-test-engineers`, masked in silver and gold.

## 7. Testing access: `ci/check_access.py`

Access control that isn't tested is a hope, not a control. Write a small script that logs in as each test SP
(OAuth M2M, client id + secret from `terraform output -json test_principal_credentials`) with
`databricks-sql-connector` and runs a table of expectations:

| Principal | Query | Expected |
|---|---|---|
| kunde_10001 | `select count(distinct customer_id) from kundeportal.kp_invoices` | `1`, and the id is 10001 |
| kunde_10001 | `select * from kundeportal.kp_invoices where customer_id = 10002` | 0 rows (not an error: the row is invisible) |
| kunde_10001 | `select * from gold.fct_invoices` | `PERMISSION_DENIED` / `INSUFFICIENT_PERMISSIONS` |
| kunde_10001 | `select * from governance.customer_access` | denied |
| analysts | `select count(distinct customer_id) from gold.fct_invoices` | = number of customers in silver |
| analysts | `select email from gold.dim_customers limit 1` | starts with `***@` |
| analysts | `select * from silver.stg_erp__customers` | denied |
| auditors | `select email from gold.dim_customers limit 1` | clear text |
| auditors | `update gold.fct_invoices set status = 'paid'` | denied |
| auditors | `select * from gold.rpt_trial_balance version as of 1` | works |
| engineers | `select * from bronze.customers limit 1` | works, email masked |
| engineers | `insert into gold.dim_accounts ...` | denied |

- Run it with `uv run --with databricks-sql-connector python ci/check_access.py`, print a pass/fail table, and
  exit non-zero on any failure. In the CI/CD step it becomes a job that runs after every `terraform apply`.
- Include **negative tests** (things that must fail). They matter more than the positive ones. A wrong grant that
  gives too *much* access never produces an error on its own.

## 8. Lineage

- **Catalog Explorer → `gold.rpt_customer_summary` → Lineage.** Show the table graph back to `bronze.customers`
  and `bronze.invoices`, and the **column** lineage for `customer_name`. Unity Catalog captures it at runtime from
  every query, across dbt, notebooks and jobs.
- **Lineage respects permissions.** Log in as an analyst and open the same graph. Upstream silver/bronze objects
  are shown as objects you can't access (or only with `BROWSE` metadata). Lineage doesn't leak data.
- **Lineage as data** (needs `system.access`):
  ```sql
  -- Where does email end up? (PII spread)
  select distinct target_table_full_name, target_column_name
  from system.access.column_lineage
  where source_table_full_name like 'lakehouse_dev.%.customers' and source_column_name = 'email';

  -- Who read the customer register in the last 7 days?
  select event_time, user_identity.email, action_name, request_params
  from system.access.audit
  where action_name in ('getTable', 'commandSubmit')
    and request_params::string like '%dim_customers%'
    and event_date >= current_date() - 7;
  ```
  The first query answers "if we have to erase a person, which tables are affected?". The second is the access log
  an auditor or a data protection officer (personvernombud) asks for.
- Compare with dbt docs from step 3: dbt lineage comes from code and works before anything has run. UC lineage
  comes from what actually ran, at column level, including things outside dbt. Show both, and the
  `kundeportal` exposure at the end of the dbt graph.

## 9. Delta time travel as an audit trail

**What's in the history.** `describe history gold.rpt_trial_balance`: one version per dbt run, with `timestamp`,
`userName` (now `sp-dbt`, not you), `operation` (`CREATE OR REPLACE TABLE AS SELECT`, `MERGE`) and
`operationMetrics` (rows written). That is a write log you get for free. It's credible because only the pipeline
can write (section 1).

**Demos to prepare:**
1. "What did the trial balance look like when the period was signed off?" Run `replay.ps1` up to day 15, note the
   version (or time), and then run to day 30.
   ```sql
   select * from gold.rpt_trial_balance timestamp as of '2026-11-16T10:00:00' where period = '2026-10'
   except
   select * from gold.rpt_trial_balance where period = '2026-10';
   ```
   The difference is exactly the late GL lines and the back-dated postings from step 3. That ties the audit
   findings to a change you can see.
2. "Who changed the customer master data, and when?" Turn on **Change Data Feed** on the incremental/merged tables
   (`+tblproperties: {delta.enableChangeDataFeed: true}` on `stg_erp__invoices`, `stg_erp__gl_entries`), and show
   `select * from table_changes('lakehouse_dev.silver.stg_erp__invoices', 2)`: row-level `update_preimage` /
   `update_postimage`. On `table`-materialized models CDF is less useful, because every run replaces everything.
3. Restore after a mistake: `restore table gold.dim_accounts to version as of n` (as `dbx_pipeline`), and show that
   an auditor *can't* run it.

**Retention: time travel is not an archive.**
- Default: the history (`delta.logRetentionDuration`) is kept 30 days, and old data files
  (`delta.deletedFileRetentionDuration`) 7 days. After a `VACUUM`, time travel further back than that fails.
  **Predictive optimization** runs `VACUUM` automatically on managed tables, so this happens without anyone doing
  anything.
- Set `+tblproperties` in `dbt_project.yml` for `gold` and `audit`: e.g. 90 days for both. Older versions then take
  up storage for 90 days. Say what that costs: little at this volume, a lot on large fact tables.
- **The legal requirement is longer.** Bokføringsloven § 13 requires accounting records to be kept for 5 years.
  Don't stretch time travel to 5 years. Freeze the signed-off period instead:
  `create table audit.rpt_trial_balance_2026_10 deep clone gold.rpt_trial_balance version as of n`. A deep clone is
  an independent copy that `VACUUM` on the source doesn't touch. Lock it down (owner `dbx_pipeline`, auditors
  `SELECT`).
- **Time travel and the right to erasure pull in opposite directions.** If a person is erased from the source, the
  old values are still readable through time travel until the files are vacuumed. The answer: accounting data has a
  legal basis to be kept (bokføringsloven beats erasure for those records), contact data like email doesn't. So keep
  retention short on tables with contact data, and keep it out of the long-lived clones (the clone of the trial
  balance contains no personal data at all). **Check:** do row filters and masks also apply to `version as of`
  queries? Test it with an analyst SP (they should, because the policy belongs to the table, not to the version).

## 10. Interview prep: be able to explain this

**Least privilege**
- Start from nothing and add only what a role needs for its job. Defaults are deny (UC: no grant = no access, the
  filter: no match = no row).
- Grant to groups, on schemas, in Terraform, and authoritatively, so drift is visible and removed.
- Separate reading from writing: humans read, the pipeline writes, under a service principal with a name in the
  history.
- `BROWSE` for "I need to know it exists", `SELECT` for "I need the data". Auditors get read on everything they
  audit, and write on nothing.
- What's still not least privilege here, and how you'd fix it in prod: you are in `dbx_pipeline` (dev compromise),
  engineers read bronze in prod (break-glass access with an expiry instead), one catalog for dev (prod as its own
  catalog with its own grants, `lakehouse_prod`, in the CI/CD step).

**Isolation between customers**

| Layer | Mechanism | What it protects against |
|---|---|---|
| Grant | Customers can only reach the `kundeportal` schema | Customers reading internal tables at all |
| Columns | The portal views only expose customer-facing columns | Leaking internal flags and keys |
| Rows | Row filter on the gold tables, using the mapping table | One customer seeing another's rows, through any path |
| Defence in depth | The same filter in the view's `where` | Someone removing the filter from a gold table |
| Test | `ci/check_access.py` negative tests | A wrong change going unnoticed |

- **Row filter vs dynamic view.** A dynamic view puts the rule in the view, so it only protects that path. If a
  user gets `SELECT` on the table, the rule is bypassed. A row filter is attached to the table, so it applies to
  every query, view, notebook and BI tool. The table is protected no matter how you reach it.
- **One table with a filter vs one schema/catalog per customer.** Filtering scales to thousands of customers and
  keeps one model. Physical separation (a catalog per tenant, or even a workspace per tenant) is stronger and
  simpler to reason about, and some clients demand it contractually. The usual answer: filters for many small
  tenants, physical separation for the few that require it.

**Personal data (personopplysninger)**
- **Know where it is:** PII tags + `information_schema.column_tags` + column lineage = a register that stays
  up to date.
- **Data minimisation:** mask what a role doesn't need, drop columns in the portal views, don't copy personal data
  into gold reports that don't need it (`rpt_customer_summary` doesn't need `email`).
- **Purpose and legal basis:** auditors see clear text because the audit requires it (revisorloven, and their duty
  of confidentiality). Analysts don't, because their purpose doesn't.
- **Masking is pseudonymisation, not anonymisation.** `***@firma.no` is still personal data under GDPR, and so is a
  hash of an email. The data is still in scope.
- **Logging:** `system.access.audit` shows who read what. That's what you show the data protection officer.
- **Erasure vs retention:** see section 9. Bokføringsloven gives a legal basis to keep accounting records, not
  contact data.
- **Location:** the data is in an Azure region in the EU/EEA (check `var.location` in `01-azure`). Mention the data
  processing agreement (databehandleravtale) between the audit firm and its clients.

## 11. Order of work and checkpoints

These are **work steps**, numbered separately from the sections above. Each one names the section it implements.

1. (section 2) Account provider, `identity.tf`, `var.databricks_account_id`, `var.portal_customers`. **Check:**
   `terraform plan` shows only additions. After apply, groups and SPs exist and are assigned to the workspace.
2. (section 3) New schemas `governance` and `kundeportal`, `grants.tf` with `databricks_grants`,
   `databricks_permissions` for the warehouse and job, ownership to `dbx_pipeline`, `system.access`. **Check:** grant
   something by hand in Catalog Explorer, then `terraform plan` shows it as drift and `apply` removes it.
3. (section 3) `run_as` on the job, plus the `owner to dbx_pipeline` post-hook. **Check:** a job run is green,
   and `describe history` on a gold table shows `sp-dbt` as the writer.
4. (section 4) The seed `customer_access`, the macro with the functions, and the `kundeportal` views + exposure.
   **Check:** `dbt build` is green, and the functions exist in `governance`.
5. (section 5) Row filters on the five gold tables (native config or post-hook, decided after checking the
   adapter). **Check:** the pipeline exemption demo (the reconciliation test fails without it), and the customer
   checks in section 5.
6. (section 6) Masks and PII tags on silver, the snapshot, gold, and `bronze.customers`. **Check:** the
   `column_tags` query lists every PII column, and the analyst/auditor checks in section 6 pass.
7. (section 7) `ci/check_access.py` with the full expectation table. **Check:** all green. Then break one thing
   on purpose (remove `kunde_10001` from the filter's mapping, or add a grant on gold) and see the right test go red.
8. (section 8) Lineage in Catalog Explorer as yourself and as an analyst, plus the two `system.access` queries.
   **Check:** the PII spread query lists exactly the tables in the PII register.
9. (section 9) Retention `tblproperties`, CDF, replay to day 15 → note version → day 30, the `except` query, the
   deep clone of the signed-off period, and the time travel + mask check. **Check:** the difference between the
   versions matches the late/back-dated rows in `_manifest.json`.
10. (section 10) Write the grant matrix, the PII table and your own short answers in the README, in your own words.

**Cost:** groups, grants, functions and filters are free. Row filters and masks add a little query time. The test
script and the demos use the same auto-stopping warehouse. Longer retention costs some storage. Test SP secrets
should be deleted (or the SPs destroyed) when you're done with them.

**Next step:** orchestration. Run dlt and dbt as tasks in one Databricks job under `sp-dbt`, with dlt first and
then `dbt build`. Then `ci/check_access.py` runs in CI/CD after every `terraform apply`.
