# Steg 3: Modellering med dbt core (silver og gold)

Goal: build silver (cleaned, deduplicated, typed) and gold (trial balance, aged accounts receivable, customer
summaries) on top of `lakehouse_dev.bronze` with **dbt Core** and the `dbt-databricks` adapter. Along the way you
should be able to explain incremental models, snapshots (SCD2), generic vs. singular tests, why accounting
controls belong in the pipeline, what goes in each medallion layer, and where dbt fits next to Spark and
Databricks-native transformations.

## 0. What already exists from step 1 and 2

- Schemas `bronze`, `silver`, `gold` in `lakehouse_dev`, and the serverless SQL warehouse `wh-dbxprosjekt`.
- A Databricks job **`dbt-build`** (`infra/02-databricks/main.tf`). It checks out the repo, runs
  `dbt deps` + `dbt build` in `dbt/` on the warehouse, and installs `dbt-databricks>=1.9,<2.0`. It is paused by
  default. This step writes the project that job runs.
- Bronze tables from dlt: `accounts` (replace), `customers`, `invoices`, `gl_entries` (merge), the child
  table `invoices__lines`, plus `_dlt_loads`. Every row has `_dlt_load_id` (a unix timestamp as a string) and `_dlt_id`.
- Known bronze quirks that silver must handle:

| Quirk in bronze | Where it comes from | What silver does |
|---|---|---|
| Amounts are `double` | dlt infers from JSON | Cast to `decimal(18,2)`. Never sum money as floats; debit = credit tests fail on rounding. |
| `invoice_date`, `due_date`, `entry_date` are text | dlt doesn't detect plain dates | Cast to `date` |
| `amount_incl_vat__v_text` (`"37 322,00"`) | type change on day 15 | Parse it and `coalesce` with `amount_incl_vat` |
| `currency`, `kid`, `lines` missing before day 15 | schema change | `currency` defaults to `'NOK'`; v1 invoices have no lines |
| `org_no = ''`, `email` null, `due_date` null, `vat_amount` null | null mess | `nullif(trim(...), '')`, derive `due_date` from payment terms, derive VAT as incl. − ex. VAT, and flag rather than drop |
| Duplicates and stale versions (after the `append` experiment) | duplicate mess | `qualify row_number() over (partition by key order by updated_at desc, _dlt_load_id desc) = 1` |
| GL lines arriving 1–5 days late | late mess | Incremental filter on **arrival** (`_dlt_load_id`), not `updated_at` (section 6) |

## 1. dbt Core or dbt Fusion?

**Choice: dbt Core + `dbt-databricks`, installed with uv.** Reasons:
- The existing `dbt_task` in the Databricks job runs dbt Core with the adapter from PyPI. Using Core locally means
  the same engine runs on your laptop and in the job.
- Everything this step needs is mature in Core: incremental `merge`, YAML snapshots (1.9+), unit tests (1.8+),
  custom generic tests, `store_failures`, `persist_docs` to Unity Catalog, and Python models if you ever need them.

**Before you decide, check Fusion against this list:** Databricks adapter support, snapshots, unit tests, custom generic
tests, `store_failures`, `persist_docs`, Python models, and whether the Databricks `dbt_task` can run it. If something
is missing, stay on Core. You can still use the dbt VS Code extension for its language server. Be ready to say
why you chose Core: "same engine locally and in the job, nothing preview-only". dbt Cloud is out of scope.

## 2. Folder layout and naming

```
dbt/
  pyproject.toml              # uv: dbt-core, dbt-databricks (>=1.9,<2.0 like the job). link-mode = "copy" (OneDrive!)
  dbt_project.yml             # name: regnskap. Folder-level materializations, schemas, persist_docs
  packages.yml                # dbt-labs/dbt_utils
  profiles.example.yml        # committed. Copy to dbt/profiles.yml (gitignored)
  macros/
    generate_schema_name.sql  # use +schema as-is: "silver", not "silver_silver"
    parse_nok_amount.sql      # '37 322,00' -> 37322.00
    dlt_loaded_at.sql         # _dlt_load_id -> timestamp (arrival time)
  models/
    staging/erp/              # SILVER: 1:1 with bronze tables. Clean, type, dedup
      _erp__sources.yml       # source 'erp' = lakehouse_dev.bronze, with freshness
      _erp__models.yml        # descriptions + tests
      stg_erp__accounts.sql
      stg_erp__customers.sql
      stg_erp__invoices.sql           # incremental, merge on invoice_id
      stg_erp__invoice_lines.sql
      stg_erp__gl_entries.sql         # incremental, merge on entry_id
    intermediate/             # SILVER: reusable logic that isn't a business model yet
      int_gl_vouchers.sql             # one row per voucher: sum debit, sum credit, is_balanced, last arrival
      int_ar_open_items.sql           # AR sub-ledger from GL: open amount on 1500 per invoice
    marts/finance/            # GOLD: business models
      _finance__models.yml    # descriptions, contracts, tests
      _finance__exposures.yml # e.g. "Revisjonsrapport" so it shows up at the end of the lineage
      dim_accounts.sql                # from snapshot: SCD2 chart of accounts
      dim_customers.sql               # from snapshot: SCD2, valid_from/valid_to/is_current
      fct_gl_entries.sql              # incremental. Only balanced vouchers
      fct_invoices.sql                # point-in-time join to the customer version valid on invoice_date
      rpt_trial_balance.sql           # saldobalanse
      rpt_ar_aging.sql                # aldersfordelt kundereskontro
      rpt_customer_summary.sql        # oppsummering per kunde
  snapshots/
    snp_erp__customers.yml    # timestamp strategy on updated_at
    snp_erp__accounts.yml     # check strategy (accounts have no updated_at)
  seeds/
    accounting_periods.csv    # period, start, end, status (open/closed), closed_at
    _seeds.yml
  tests/
    generic/                  # reusable custom tests (used in YAML like not_null)
      test_balanced_by.sql
      test_no_sequence_gaps.sql
      test_not_in_closed_period.sql
    assert_ar_subledger_reconciles_to_gl.sql
    assert_trial_balance_nets_to_zero.sql
    assert_no_double_billing.sql
    assert_no_postings_to_inactive_accounts.sql
  scripts/
    replay.ps1                # day-by-day: dlt --day N, then dbt build (section 7)
```

**Naming rules (write them in the README so you can defend them):**
- `stg_<source>__<entity>`: one per bronze table, no joins, no business logic. The double underscore separates source and entity.
- `int_<what>`: intermediate building blocks, never exposed to end users.
- `dim_` / `fct_`: dimensions (who/what, SCD2) and facts (events: postings, invoices) in gold.
- `rpt_`: aggregated report models with a fixed grain, the ones auditors and the business read.
- `snp_`: snapshots. Column names in English, Norwegian accounting terms in descriptions (`saldobalanse`,
  `kundereskontro`, `bilag`) and in a doc block glossary.
- Every model folder has one `_<folder>__models.yml`. Tests and docs sit next to the SQL.

**`dbt_project.yml` (sketch):**
```yaml
models:
  regnskap:
    +persist_docs: {relation: true, columns: true}   # descriptions show up in Catalog Explorer
    staging:      {+schema: silver, +materialized: table}
    intermediate: {+schema: silver, +materialized: view}
    marts:        {+schema: gold,   +materialized: table}
snapshots:
  regnskap: {+schema: silver}
seeds:
  regnskap: {+schema: silver}
data_tests:
  regnskap: {+store_failures: true, +schema: audit}   # failing rows are kept as evidence
```

**Small changes outside `dbt/`:**
- `.gitignore`: add `!dbt/seeds/*.csv`. `*.csv` is ignored today, so the seed would never be committed.
- `infra/02-databricks/variables.tf`: add `"audit"` to `schemas`, so `store_failures` has a schema that
  `terraform destroy` removes too. **Check:** `terraform plan` shows one schema added.
- Delete `dbt/.gitkeep`.

## 3. Connecting to Databricks

`dbt/profiles.yml` (gitignored, copied from `profiles.example.yml`):
```yaml
regnskap:
  target: dev
  outputs:
    dev:
      type: databricks
      catalog: lakehouse_dev
      schema: silver               # default. Folders override it with +schema
      host: "{{ env_var('DATABRICKS_HOST') }}"          # terraform output workspace_url
      http_path: "{{ env_var('DATABRICKS_HTTP_PATH') }}" # terraform output sql_warehouse_http_path
      auth_type: oauth             # browser login (U2M), token cached. No PAT on disk
      threads: 4
```
- Run everything from `dbt/` with `uv run dbt ...`. dbt finds `profiles.yml` in the current directory.
- Check whether your adapter version can also use `az login` like dlt does. If not, `oauth` is the closest
  equivalent. A PAT via `env_var('DATABRICKS_TOKEN')` is the fallback.
- In the Databricks job, `dbt_task` generates its own profile from `warehouse_id`, `catalog` and `schema`, so
  `profiles.yml` is never needed there. That's why it's fine to keep it out of Git.
- **`generate_schema_name`:** dbt's default puts models in `<target schema>_<custom schema>` (`silver_gold`).
  Override it to use the custom schema as-is, because the schemas come from Terraform. In a team you'd keep the
  prefix in dev (one schema per developer) and drop it in prod. Be ready to explain that.

**Check:** `uv run dbt debug` says "All checks passed!".

## 4. Seed: accounting periods (and new "audit mess" in the generator)

The clean generator data never breaks an accounting control, so the custom tests would always pass and prove
nothing. Add three toggleable steps to `ingestion/generator/mess.py`, in the same style as step 2. Each one gets its
own seeded RNG, so existing mess is unchanged, and each one writes its counts to `_manifest.json`. **The manifest is
the answer key for the audit tests.**

| New mess step | What it does | Which test should catch it |
|---|---|---|
| `invoice_gap` | Removes every row for one invoice in the middle of the month, plus its GL vouchers | No gaps in the invoice numbers (and in voucher numbers) |
| `double_billing` | Re-issues one invoice a few days later with a new number, the same customer, the same lines and amount, plus a matching GL voucher | No double invoices (business duplicate) |
| `backdated` | 2 expense vouchers posted on day 8 with `entry_date` 2026-09-30, in a period that was closed on 2026-10-05 | No postings in closed periods |

Run order: `schema_change → nulls → invoice_gap → double_billing → backdated → late → duplicates`. New vouchers
get the next free voucher number, so voucher numbers stay gap-free except where `invoice_gap` removes some.
Update `ingestion/tests/test_generator.py`: vouchers still balance, AR (1500) still reconciles, and each new step
does what it reports.

Also note that 6940 Porto is deactivated on day 10, but the generator keeps posting to it. That's a free fourth
finding: "postings to inactive accounts".

`seeds/accounting_periods.csv`:
```csv
period,period_start,period_end,status,closed_at
2026-09,2026-09-01,2026-09-30,closed,2026-10-05T16:00:00Z
2026-10,2026-10-01,2026-10-31,open,
2026-11,2026-11-01,2026-11-30,open,
```
In practice this table comes from the ERP's period closing. As a seed, it's versioned and visible in the lineage.
Mention VAT periods (terminer) and year-end close as the real reasons periods get locked.

## 5. Silver: clean, dedup, type

`_erp__sources.yml` defines source `erp` → `lakehouse_dev.bronze`, with
`loaded_at_field: "{{ dlt_loaded_at('_dlt_load_id') }}"` and freshness `warn_after: 24h`. That measures real load
time. The data's own dates are simulated, so freshness on `updated_at` would always fail.

| Model | Materialization | Key points |
|---|---|---|
| `stg_erp__accounts` | table | Types, `account_class`, `is_active`. Small, so a full rebuild every time. |
| `stg_erp__customers` | table | `nullif(trim(org_no), '')`, a flag `is_valid_org_no` (mod 11 check digit, as a macro), lower-case email, dedup on `customer_id` |
| `stg_erp__invoices` | **incremental merge** on `invoice_id` | `coalesce(amount_incl_vat, parse_nok_amount(amount_incl_vat__v_text))`, dates as `date`, `due_date` fallback flagged `is_due_date_derived`, `vat_amount` fallback, `currency` default, dedup within the batch |
| `stg_erp__invoice_lines` | table | Join `invoices__lines._dlt_parent_id = invoices._dlt_id` to get `invoice_id`. Only v2 invoices have lines; document that. |
| `stg_erp__gl_entries` | **incremental merge** on `entry_id` | `debit`/`credit` as `decimal(18,2)`, `entry_date` as `date`, `posted_at` (= `updated_at`), `arrived_at` (= `dlt_loaded_at`), dedup within the batch |
| `int_gl_vouchers` | view | One row per `voucher_no`: line count, sum debit, sum credit, `is_balanced`, `last_arrived_at` |
| `int_ar_open_items` | view | Per `invoice_id`: sum(debit − credit) on account 1500 up to `as_of_date`, i.e. the AR sub-ledger built from the GL |

**Principle: flag, don't silently drop.** A row with an invalid org.nr is kept with `is_valid_org_no = false`, and a
`warn` test reports it. Dropping rows in silver hides problems from the people who should fix them at the source.

## 6. Incremental models

```sql
-- stg_erp__gl_entries.sql (sketch)
{{ config(materialized='incremental', incremental_strategy='merge',
          unique_key='entry_id', on_schema_change='append_new_columns') }}

select ... from {{ source('erp', 'gl_entries') }}
{% if is_incremental() %}
where cast(_dlt_load_id as double) > (select max(cast(_dlt_load_id as double)) from {{ this }})
{% endif %}
qualify row_number() over (partition by entry_id order by updated_at desc, _dlt_load_id desc) = 1
```

- **Filter on arrival, not on `updated_at`.** This is the same cursor trap as in step 2: late GL rows have an old
  `updated_at`, and `updated_at > max(updated_at)` would miss them for good. `_dlt_load_id` is set by the loader,
  so it only ever increases. Show it: write the naive version first, run day 1–12, and count missing rows against bronze.
- **Merge on the key** makes re-runs and overlap harmless, the same idempotency argument as dlt's merge.
- **Strategies on Databricks to know about:** `append` (cheapest, but you get duplicates), `merge` (upsert on a key),
  `insert_overwrite` (overwrites partitions), `replace_where` (overwrites the rows matching a predicate, for
  example the last N days), and `microbatch` (dbt 1.9+, splits work by an event time column. Late data needs a
  `lookback`, the same issue as dlt's `lag`).
- **When it breaks:** after a logic change, run `dbt build --full-refresh -s stg_erp__gl_entries+`. Incremental
  models only process new rows, so old rows keep the old logic until you do a full refresh.
- **Gold `fct_gl_entries` is also incremental**, and it only takes lines from vouchers where `is_balanced`. A voucher
  with a late line waits in silver (and shows up in the warn test) until the missing line arrives. The next run
  then picks up the whole voucher, because the filter selects vouchers with *any* line arriving since the last run.
  That's quarantine, not deletion: gold never shows a trial balance with half a voucher.

## 7. Snapshots (SCD2)

```yaml
# snapshots/snp_erp__customers.yml
snapshots:
  - name: snp_erp__customers
    relation: source('erp', 'customers')
    config:
      unique_key: customer_id
      strategy: timestamp
      updated_at: updated_at
      dbt_valid_to_current: "cast('9999-12-31' as timestamp)"
```
- `snp_erp__accounts`: `strategy: check` with `check_cols: [name, account_type, vat_code, is_active]`, because
  `accounts` has no `updated_at`. The day 10 change (6860 added, 6940 deactivated) becomes a new version.
- Snapshot the **source**, with at most light cleaning. A snapshot is history you can't rebuild. If cleaning logic
  changes later, you want the raw values preserved. Cleaning happens downstream in `dim_customers`.
- **The big limitation:** a snapshot only sees the state at the moment it runs. Bronze is merged, so if a customer
  changes twice between two runs, the middle version is lost. That's why snapshots must run after every load.
  `scripts/replay.ps1` does that: for each simulated day it sets the API clock, runs `pipelines.regnskap --day N`,
  then `dbt build`. To limit warehouse cost, replay a few key days (1, 5, 9, 10, 15, 20, 30) rather than all 30.
  Alternatives if you need every version: dlt's `scd2` merge strategy in bronze, or `append` in bronze plus SCD2
  built from the history.
- **Where it's used:** `dim_customers` has `valid_from`, `valid_to` and `is_current`. `fct_invoices` joins the customer
  version that was valid on `invoice_date`: the address and payment terms *at the time of invoicing*. That's what an
  auditor wants to see. The inactive account test uses `dim_accounts` the same way.

## 8. Gold: business models

**`rpt_trial_balance` (saldobalanse).** Grain: account × period (month).
- Columns: `period`, `account_no`, `account_name`, `account_type`, `account_class`, `opening_balance`, `debit`,
  `credit`, `net_change`, `closing_balance`.
- Balance sheet accounts (class 1–2) carry their balance forward. P&L accounts (class 3–8) start from 0 each
  financial year.
- A period × account spine (from the seed × `dim_accounts`), so accounts with no activity still show their balance.

**`rpt_ar_aging` (aldersfordelt kundereskontro).** Grain: open invoice on `as_of_date`.
- `as_of_date` is `var('as_of_date')`, defaulting to the latest `entry_date` in the data, *not* `current_date()`.
  The data is simulated in October, so "today" would age everything by weeks.
- Open amount comes from `int_ar_open_items` (the GL), not from the invoice status.
- `days_past_due = as_of_date − due_date`. Buckets: `not_due`, `1-30`, `31-60`, `61-90`, `90+`, as an accepted
  values test. A second view pivots it per customer, with one column per bucket.

**`rpt_customer_summary` (oppsummering per kunde).** Grain: customer (current version).
- Number of invoices, invoiced amount incl. VAT, credited, paid, open, overdue, average days to payment (payment
  voucher `entry_date` − `invoice_date`), last invoice date, and share of open AR that is overdue.

**Contracts:** set `contract: {enforced: true}` on the three `rpt_` models, with data types and `not_null`. Then a
change in column name or type breaks the build instead of silently breaking the dashboards and reports that read
them. Mention that this is the "freeze" from dlt's schema contracts, at the other end of the pipeline.

## 9. Tests

**Two kinds of test, two severities. This is the most important design decision in the step:**

| Kind | Examples | Severity | Why |
|---|---|---|---|
| **Structural / data contract** | `unique`, `not_null`, `relationships`, `accepted_values`, trial balance nets to zero, AR reconciles | `error` | If these fail, the models are wrong. `dbt build` then skips everything downstream, so nothing wrong reaches gold. |
| **Accounting controls (findings)** | debit = credit per voucher in silver, no gaps, no double invoices, no closed period postings, no inactive accounts | `warn` + `store_failures` | They report problems *in the source data*. The pipeline should keep running and deliver the findings to an auditor, not stop. The failing rows are stored in `lakehouse_dev.audit`. |

**Generic tests (YAML):**
- `unique` + `not_null`: every primary key (`customer_id`, `invoice_id`, `entry_id`, `account_no`),
  `dbt_utils.unique_combination_of_columns` on `[voucher_no, line_no]` and on the trial balance's `[period, account_no]`.
- `relationships`: `invoices.customer_id → stg_erp__customers`, `gl_entries.account_no → stg_erp__accounts`,
  `gl_entries.invoice_id → stg_erp__invoices` (with `where: invoice_id is not null`), `fct_invoices.customer_sk → dim_customers`.
- `accepted_values`: invoice `status` (`sent`, `overdue`, `paid`, `credited`), `account_type`, `aging_bucket`.
- `dbt_utils.expression_is_true`: `debit >= 0 and credit >= 0`, `not (debit > 0 and credit > 0)`,
  `amount_incl_vat = amount_ex_vat + vat_amount`.

**Custom generic tests (`tests/generic/`), the accounting controls:**
```yaml
- name: stg_erp__gl_entries
  data_tests:
    - balanced_by: {group_by: voucher_no, debit: debit, credit: credit}            # debit = credit per voucher
    - not_in_closed_period: {date_column: entry_date, posted_column: posted_at}    # uses ref('accounting_periods')
  columns:
    - name: voucher_no
      data_tests: [no_sequence_gaps]
- name: stg_erp__invoices
  columns:
    - name: invoice_id
      data_tests: [unique, no_sequence_gaps]                                       # no gaps in invoice numbers
```
- `balanced_by` returns the vouchers where `sum(debit) <> sum(credit)`. It's exact because the amounts are decimals.
  Expect `warn` hits during the replay (late lines). After the last day they must be 0, because the
  late lines have arrived. That's a good thing to show live.
- `no_sequence_gaps` uses `lead()` to return each gap as `from`–`to`. That's what the auditor wants: *which* numbers are missing.
- `not_in_closed_period` returns rows whose `entry_date` is in a closed period **and** were posted after `closed_at`.
  Postings made before closing are fine.

**Singular tests (`tests/*.sql`):**
- `assert_ar_subledger_reconciles_to_gl`: the sum of `rpt_ar_aging` = the GL balance on 1500 on `as_of_date`, and the
  open amount per invoice from the GL = the amount on invoices whose status is `sent`/`overdue`. That's the classic
  sub-ledger vs. general ledger reconciliation. **error.**
- `assert_trial_balance_nets_to_zero`: the sum of `closing_balance` over all accounts = 0 for each period. **error.**
- `assert_no_double_billing`: invoices with the same customer, the same `amount_incl_vat` and the same line content
  within 14 days, but different numbers. **warn.** The distinction matters: `unique` on `invoice_id` catches
  *technical* duplicates (the same row twice, the ones bronze/silver dedup handles). This test catches *business*
  duplicates: two valid invoices that shouldn't both exist.
- `assert_no_postings_to_inactive_accounts`: GL lines where the account version valid on `entry_date` (from
  `dim_accounts`) has `is_active = false`. **warn.**

**Unit tests (dbt 1.8+)** with given input and expected output, no warehouse data needed:
- `parse_nok_amount` via `stg_erp__invoices`: `"37 322,00"` → `37322.00`, `"1 234,50"` → `1234.50`, and a number unchanged.
- Aging buckets in `rpt_ar_aging`: 0, 1, 30, 31, 90 and 91 days past due land in the right buckets (boundary values).

**Check:** with `--mess all`, `dbt build` finishes with only `warn` failures, and the number of hits per audit test
matches `_manifest.json`. With `--mess none`, everything passes.

## 10. Docs and lineage

- Descriptions on every model and column, in YAML. A `docs/glossary.md` doc block explains `bilag`, `saldobalanse`,
  `kundereskontro`, `KID`, `NS 4102` and periods. Reuse it with `{{ doc('saldobalanse') }}`.
- `uv run dbt docs generate` and then `uv run dbt docs serve`. Show the lineage graph from `source:erp.gl_entries`
  through `stg_` → `int_` → `fct_` → `rpt_` → exposure `Revisjonsrapport`. Use `+` selection in the graph
  (`+rpt_ar_aging`) to show "everything this report depends on".
- `persist_docs` writes the descriptions as comments in Unity Catalog. Open `gold.rpt_trial_balance` in Catalog
  Explorer and look at the **Lineage** tab. Unity Catalog shows **column-level** lineage captured at runtime,
  automatically and across tools. dbt shows model-level lineage from code, and it also works before anything has
  run. Be able to explain both.
- Selectors to know: `dbt build -s staging.erp+`, `-s tag:audit`, `-s state:modified+ --defer` (saved for CI in step 5).
- Publishing the docs as a static site belongs in the CI/CD step. For now, `target/` stays local (gitignored).

## 11. Interview prep: be able to explain this

**What goes in each medallion layer, and why**

| Layer | Contents here | Rule | Why |
|---|---|---|---|
| **Bronze** | dlt tables as the source sent them, plus `_dlt_*` | Append or merge, no business logic, schema `evolve` | Replay and audit trail: you can always rebuild silver/gold from bronze, and prove what the source actually sent |
| **Silver** | `stg_`/`int_`, snapshots, seeds | One row per business key, correct types, standardised values, flags instead of deletions, history (SCD2) | A trusted, reusable base. Many gold models share one cleanup, so it's written once |
| **Gold** | `dim_`/`fct_`/`rpt_` | Business grain and business terms, contracts, aggregates | Answers questions directly (trial balance, AR aging), is stable for consumers, and is the part you put access control on |

A short way to say it: bronze = *what we received*, silver = *what is true*, gold = *what it means*. Accounting
controls are silver-level tests, because that's where the data is clean enough to test and early enough to stop
bad data before it reaches gold.

**Where dbt fits compared to Spark or Databricks-native transformations**

| | dbt (on Databricks) | Spark / PySpark notebooks or jobs | Lakeflow Declarative Pipelines (formerly DLT) |
|---|---|---|---|
| Language | SQL + Jinja (Python models possible) | Python/Scala/SQL, full control | SQL or Python, declarative |
| Compute | None of its own. Compiles SQL and sends it to the SQL warehouse | Clusters / serverless | Managed pipeline compute |
| Strengths | Tests, docs, lineage, refs/DAG, SQL-friendly for analysts, portable between warehouses, Git/CI workflow | Complex logic, ML, large unstructured data, streaming, custom algorithms | Streaming tables, automatic incremental processing, expectations, CDC (`AUTO CDC`), managed orchestration |
| Weaknesses | Batch-oriented, logic in Jinja can get messy, not built for streaming | No built-in tests, docs or lineage, so more code to maintain | Databricks-specific (lock-in), less portable |
| Here | Silver/gold modelling and accounting controls | Not needed: the data is tabular and small | Auto Loader comparison in step 2. Could have replaced dbt for streaming silver |

Short answer: **dbt is the T in ELT for SQL-shaped modelling.** It's strongest where tests, documentation and code
review matter more than raw compute, which is exactly the case for accounting and audit. Use Spark when the logic
doesn't fit in SQL or the data isn't tabular. Use Lakeflow when you need streaming or low latency and are happy to
be Databricks-native. They can be combined: `dbt-databricks` can create `materialized_view` and `streaming_table`
models, which Databricks then maintains incrementally.

**Talking points for audit clients**
- Every control is code, versioned in Git, runs on every load, and stores its findings (`store_failures`). That's
  *continuous auditing* instead of sampling once a year.
- Full population, not samples: the gap test checks *every* invoice number.
- Traceability: gold row → `fct_gl_entries` → `stg_` → bronze `_dlt_load_id` → `_dlt_loads` → the API call.
- SCD2: "What did the customer master data look like on the invoice date?" can be answered.

## 12. Order of work and checkpoints

These are **work steps**, numbered separately from the sections above. Each one names the section it implements.

1. (section 4) New mess steps in `mess.py` and tests in `test_generator.py`. Regenerate the data and reload bronze
   with `--refresh`. **Check:** `uv run pytest` is green, and `_manifest.json` has counts for the three new steps.
2. (section 2–3) `dbt/pyproject.toml`, `dbt_project.yml`, `packages.yml`, `profiles.yml`, `generate_schema_name`,
   `.gitignore` exception, and the `audit` schema in Terraform. **Check:** `uv run dbt debug` and `terraform plan` (only an addition).
3. (section 5) Sources with freshness and the `stg_` models as plain tables first. **Check:** `dbt build -s staging`,
   with row counts in silver = distinct keys in bronze, and `dbt source freshness`.
4. (section 6) Make `stg_erp__gl_entries` and `stg_erp__invoices` incremental. Show the naive `updated_at` filter first,
   then the `_dlt_load_id` filter. **Check:** running twice changes nothing, and missing = 0 after day 1–12.
5. (section 4, 7) Seed and snapshots, then `replay.ps1` over the key days. **Check:** customer 100xx with an address
   change has two rows in `snp_erp__customers`, and 6940 gets a new version on day 10.
6. (section 8) `int_`, `dim_`, `fct_`, `rpt_` models with contracts. **Check:** the trial balance nets to zero, and the
   AR aging sum = the GL balance on 1500.
7. (section 9) Generic, custom generic, singular and unit tests. **Check:** with `--mess all` only `warn`s, matching the manifest.
   With `--mess none` everything passes. Look at the failing rows in `lakehouse_dev.audit`.
8. (section 10) Descriptions, glossary, exposure, docs. **Check:** `dbt docs serve` shows the full lineage, and Catalog
   Explorer shows the descriptions and the column lineage.
9. (section 0) Run the existing `dbt-build` job once by hand (Run now) against the same bronze. **Check:** green run,
   and the same results as locally.
10. (section 11) Write your own short answers in the README, in your own words.

**Cost:** dbt itself is free. Only the SQL warehouse costs money while it runs, and it has auto-stop. A full
`dbt build` on this data volume takes a minute or two. Keep the replay to the key days, and keep the job schedule paused.

**Next step:** orchestration. Run dlt and dbt as tasks in one Databricks job, with dlt first and then `dbt build`. The
fake API runs locally today, so either host it (a Databricks App or a container), or let the dlt task read the
files in the `landing` volume instead.
