# Steg 2: Syntetiske regnskapsdata og ingest med dlt til bronze

Goal: generate messy but realistic accounting data, expose it through a fake source, and load it into
`lakehouse_dev.bronze` with **dlt (data load tool)**. Along the way you should be able to explain full vs.
incremental loads, idempotency, schema evolution, dlt state, and when you'd pick dlt over Auto Loader / Lakeflow.

> **Name clash, learn this first:** `dlt` (dlthub, "data load tool") is an open-source Python library.
> **DLT** (Delta Live Tables) is Databricks' old name for what is now **Lakeflow Declarative Pipelines**.
> They have nothing to do with each other. In an interview, say "dlthub" or "data load tool" so nobody is confused.

## 0. What already exists from step 1

- Catalog `lakehouse_dev` with schemas `bronze`, `silver`, `gold` (`infra/02-databricks`).
- Serverless SQL warehouse `wh-dbxprosjekt`. dlt's Databricks destination will load through it.
- The lake in ADLS Gen2 has **shared keys disabled**. That rules out staging via storage account keys, so we
  stage through a **Unity Catalog Volume** instead (section 5).

## 1. Architecture

```
generator (Faker, seed)  ──►  ingestion/data/  (JSONL per simulated day, gitignored)
                                   │
              ┌────────────────────┴─────────────────────┐
              ▼                                          ▼
   fake REST API (FastAPI, local)            files uploaded to UC Volume
   /accounts /customers /invoices            /Volumes/lakehouse_dev/bronze/landing/
   /gl-entries  (paging, updated_since)                 │
              │                                          ▼
              ▼                                Auto Loader notebook (comparison only)
   dlt pipeline (local Python)                  → bronze.al_invoices
   → lakehouse_dev.bronze.*  (+ _dlt_* tables)
```

The main path is **REST API → dlt → bronze**. The same files also land in a Volume, so you can run Auto Loader
on *exactly the same data* and compare the two approaches side by side. That comparison is the best possible
interview prep for "why dlt instead of Auto Loader?".

## 2. Folder layout

```
ingestion/
  pyproject.toml            # uv project: faker, fastapi, uvicorn, dlt[databricks], pytest
  generator/
    model.py                # dataclasses for the 4 entities
    generate.py             # CLI: python -m generator.generate --days 30 --seed 42
    mess.py                 # all the "realistic mess" in one place, each kind toggleable
  fake_api/
    app.py                  # FastAPI, reads ingestion/data/, simulated clock
  .dlt/config.toml          # non-secret dlt config (committed)
  .dlt/secrets.toml         # workspace hostname + warehouse http_path (gitignored!)
  pipelines/
    databricks_setup.py     # shared destination: az login auth, JSONL file format
    smoke_test.py           # section 5 check: DuckDB or Databricks
    regnskap.py             # dlt source + resources + pipeline run
  notebooks/
    autoloader_compare.py   # Databricks notebook (source format) for the Auto Loader comparison
  tests/
    test_generator.py       # e.g. each GL voucher balances, ids are unique before mess is added
```

Add `ingestion/data/` and `**/.dlt/secrets.toml` to `.gitignore` before anything else.

## 3. The data generator (Faker)

Use `Faker("no_NO")` and a fixed seed so every run is reproducible. Generate **day by day** (e.g. 30 days),
because incremental loading only makes sense when data arrives over time.

| Entity | Key | Content | Notes |
|---|---|---|---|
| **kontoplan** (chart of accounts) | `account_no` | ~40 accounts based on NS 4102: 1500 Kundefordringer, 1920 Bank, 2700 Utgående mva, 3000 Salgsinntekt, 4000 Varekjøp, 6300 Leie lokale, ... On day 10, 6860 is added and 6940 is deactivated. | Small, rarely changes, and has no `updated_at`. Natural candidate for a **full load**: the day 10 change is only picked up by a full load. |
| **kunder** (customers) | `customer_id` | name, org.nr (9 digits), address, email, payment terms, `updated_at` | Customers get updated (address, terms), so you need **merge/upsert**. |
| **fakturaer** (invoices) | `invoice_id` | customer_id, invoice_date, due_date, amount ex. VAT, VAT, status (`sent`/`paid`/`credited`), `updated_at` | Status changes over time, so the same invoice is sent again with a new `updated_at`. |
| **hovedbok** (GL entries) | `entry_id` (voucher no. + line no.) | voucher_no, entry_date, account_no, debit/credit, amount, reference to invoice_id, `updated_at` | Every voucher must **balance** (sum of debit = sum of credit). An invoice produces 1500 debit / 3000 + 2700 credit. A payment produces 1920 debit / 1500 credit. |

Generating the GL entries from invoices and payments, rather than at random, makes silver/gold in step 3
meaningful: accounts receivable in the GL should reconcile against unpaid invoices.

### The mess (`mess.py`)

Each kind of mess is a separate function, so you can turn it off and see the difference.

| Mess | How | What it teaches |
|---|---|---|
| **Duplicates** | ~2 % of rows are sent twice: in the same page, and again on the next day | Why `primary_key` + merge matters, and that `append` gives duplicates |
| **Late-arriving rows** | ~3 % of GL entries show up 1–5 days late, *with an `updated_at` that is older than what you've already loaded* | The classic cursor trap: a naive `updated_at > last_value` misses them. Fix with `lag` or a source-side sequence (section 6) |
| **Nulls** | missing email, missing `due_date`, missing VAT on some invoices, empty org.nr | Bronze accepts everything as-is; tests and cleanup belong in silver (dbt) |
| **Schema change midway** | From day 15, invoices are "v2": new fields `currency` and `kid`, `lines` as a nested list, and `amount_incl_vat` comes back as a string `"1 234,50"` for ~5 % of rows | Shows dlt's schema evolution: new columns, child tables, variant columns (section 7) |

Updates (invoice status `sent` → `overdue` → `paid` or `credited`, customers changing address) aren't mess.
They're real source behaviour, and `generate.py` produces them. They're the reason you need merge/upsert.

`--mess none` turns all mess off. With mess off there's no schema change either, so every invoice uses the v2
shape. Each step's counts are written to `data/_manifest.json`, which is your "expected result" when you check bronze.

**Check:** `uv run pytest` (from `ingestion/`) passes. The tests check that every voucher balances,
that accounts receivable (1500) reconcile against open invoices, and that each kind of mess does what it reports.

## 4. The fake source

### REST API (main path)

A small FastAPI app that reads `ingestion/data/` and pretends to be an ERP system:

- `GET /accounts` returns the latest chart of accounts snapshot with no paging and no `updated_at` (so it gets a full load).
- `GET /customers?updated_since=...&page=N&page_size=100`
- `GET /invoices?updated_since=...&page=N&page_size=100`
- `GET /gl-entries?updated_since=...&page=N&page_size=100`
- Paging: `{"data": [...], "page": 2, "total": 331, "total_pages": 4, "next_page": 3}` (`next_page: null` on the
  last page). The default page size is 100, so you actually exercise paging. In dlt, this fits
  `PageNumberPaginator(base_page=1, total_path="total_pages")`.
- `updated_since` is **inclusive** (`updated_at >= updated_since`), so rows exactly on the cursor come back
  again. That's common in real APIs. dlt handles it (incremental deduplicates on the cursor boundary).
- Rows are sorted by `updated_at` and then the key, so paging is stable.
- **Simulated clock:** the API only serves rows from day partitions `<= SIMULATED_DAY`. Set it with an env var
  (start value), with `POST /_clock {"day": 7}`, or step forward with `POST /_clock {"advance": 1}`. Read it with `GET /_clock`. That lets you run the pipeline "day 1", "day 2", ... and watch incremental
  loading happen one step at a time. The clock filters on the **arrival day** (the partition), *not* on
  `updated_at`. That's what makes late rows possible: they show up on day 8 with an `updated_at` from day 5.

Run it locally with `uv run uvicorn fake_api.app:app --reload`. dlt also runs locally, and writes to Databricks.

### Files (for the Auto Loader comparison)

The generator always writes `data/<entity>/day=YYYY-MM-DD/part-0.jsonl`. The API reads these files, and they
can also be uploaded as-is, one day at a time, to the Volume with the Databricks CLI:
`databricks fs cp -r data/invoices/day=2026-10-15 dbfs:/Volumes/lakehouse_dev/bronze/landing/invoices/day=2026-10-15`

## 5. Databricks side: Terraform and access

**Terraform** (`infra/02-databricks/main.tf`): two managed volumes in `bronze`, made with `for_each`:

- **`landing`**: raw JSONL files for the Auto Loader comparison (section 9).
- **`dlt_staging`**: dlt's Databricks destination first uploads its load files to a volume with `PUT`, then runs
  `COPY INTO` into the table. If `staging_volume_name` isn't set, dlt runs `CREATE VOLUME _dlt_staging_load_volume`
  by itself. Making the volume in Terraform keeps it under `terraform destroy`. A managed volume lives under the
  catalog's storage root, so it needs no keys, and that's the setup that works with shared keys disabled.
- Outputs: `volume_paths` and `dlt_staging_volume_name`.

**dlt config** lives in `ingestion/.dlt/` (dlt reads `.dlt/` from the current directory, so always run from `ingestion/`):

- `config.toml` (committed): `catalog`, `staging_volume_name = "lakehouse_dev.bronze.dlt_staging"`, and
  `keep_staged_files = false` so the files are deleted after each `COPY INTO`.
- `secrets.toml` (gitignored): `server_hostname` and `http_path`, from `terraform output workspace_url` and
  `sql_warehouse_http_path`. Start by copying `secrets.toml.example`.

**Identity: `az login`, no token.** If `secrets.toml` has no `access_token` and no `client_id`/`client_secret`, dlt
falls back to the Databricks SDK's *unified auth*. `pipelines/databricks_setup.py` points the SDK at the workspace
(`DATABRICKS_HOST`) with `DATABRICKS_AUTH_TYPE=azure-cli`, for the running process only. That's the same login Terraform
uses, and there's no long-lived secret on disk. Alternatives are a PAT (`access_token`), or for CI/prod a
**service principal** with OAuth M2M (`client_id`/`client_secret`) and grants `USE CATALOG`, `USE SCHEMA`,
`CREATE TABLE`, `MODIFY`, `READ VOLUME`, `WRITE VOLUME` on `bronze` only.

**File format: Parquet, and why it first failed.** Parquet first failed with *"En programkontrollpolicy har
blokkert denne filen"* on pyarrow's DLL. The cause was uv **hard-linking** packages from its cache into a `.venv`
inside OneDrive. `link-mode = "copy"` in `ingestion/pyproject.toml` fixed it. (If `.venv` ever gets into a broken
state, delete it and run `uv sync`.) JSONL is still possible (`DATABRICKS_FILE_FORMAT=jsonl`), but it breaks on
date-like strings: dlt runs `COPY INTO` with `inferTimestamp=true`, Spark reads `"2026-10-01"` as a TIMESTAMP, and
the load fails with `DELTA_FAILED_TO_MERGE_FIELDS`. The format is set as the destination's *preferred* format in
`databricks_setup.destination(...)`, because `run(loader_file_format=...)` doesn't apply to dlt's own state table.

**Check (smoke test):**
```powershell
uv run python -m pipelines.smoke_test                          # local DuckDB, no cloud
uv run python -m pipelines.smoke_test --destination databricks # creates lakehouse_dev.bronze.smoke_test
uv run python -m pipelines.smoke_test --destination databricks --cleanup   # drops it again
```
In Catalog Explorer under `lakehouse_dev` → `bronze`, you'll see `smoke_test` plus dlt's system tables
`_dlt_loads`, `_dlt_pipeline_state` and `_dlt_version`, and the volumes `landing` and `dlt_staging` (which is
empty after a load).

## 6. The dlt pipeline: full, incremental, merge

```python
pipeline = dlt.pipeline(
    pipeline_name="regnskap",
    destination=destination("databricks"),   # from pipelines.databricks_setup
    dataset_name="bronze",          # = schema in the catalog
)
```

One resource per endpoint, each with a deliberately chosen strategy:

| Resource | `write_disposition` | Keys / incremental | Why |
|---|---|---|---|
| `accounts` | `replace` | none | Small table with no `updated_at`. A full load every time is simplest and always correct. |
| `customers` | `merge` | `primary_key="customer_id"`, `dlt.sources.incremental("updated_at")` | Updates must overwrite the old row (upsert). |
| `invoices` | `merge` | `primary_key="invoice_id"`, incremental on `updated_at` | Status changes and duplicates are both handled by the merge. |
| `gl_entries` | `merge` | `primary_key="entry_id"`, incremental on `updated_at` with **`lag`** | GL entries are in practice append-only, but merge on the key makes re-runs and duplicates harmless. |

Two ways to write the resources. Both are in `ingestion/pipelines/regnskap.py`:
1. **`rest_api_source`** (declarative config: paginator, `incremental`, `primary_key` per resource). Little
   code, and it's what you'd use in practice. This is the default for all four resources.
2. **`@dlt.resource` + `requests`** by hand: `gl_entries_handwritten`, switched on with `--handwritten-gl`. Both
   share the same cursor in state, so you can swap between them. A real bug was found here: reading
   `updated_at.last_value` *inside* the paging loop sends a new `updated_since` for page 2, because dlt moves the
   cursor while rows pass through. Read it once, before paging.

Two more details that matter:
- **`dedup_sort: desc` on `updated_at`** for `customers` and `invoices`. When an invoice changes status twice between
  two runs (sent → paid), both versions arrive in the same load. Without `dedup_sort`, dlt can't know which one should win.
- **`lag` re-reads the last N days on every run.** You can see that in "rows received" (about 100 GL rows a day even
  though only ~50 are new). The merge on `entry_id` makes the overlap harmless. Lag has to be at least the longest
  delay in the source (5 days here).

**How to run it** (from `ingestion/`, with the API running in another terminal: `uv run uvicorn fake_api.app:app`):
```powershell
uv run python -m pipelines.regnskap --day 1-3            # local DuckDB (regnskap_duckdb.duckdb)
uv run python -m pipelines.regnskap --day 1-3 --destination databricks
uv run python -m pipelines.regnskap --report             # only print the bronze vs. source table
```
After each run a table compares bronze with the source: `missing` (keys not in bronze), `dupes` (keys more than
once) and `stale` (rows holding an outdated version). All three should be 0. The pipeline is called
`regnskap_duckdb` or `regnskap_databricks`, with separate state per destination.

Things to try, one at a time (the same experiments are automated in `ingestion/tests/test_pipeline.py`):

1. **Run day 1–3.** `--day 1-3`. Look at the row counts in bronze and at `_dlt_loads`.
2. **Run the same day twice.** `--day 3`, then `--day 3` again. The table must not change (idempotency).
3. **Switch `invoices` to `append`.** `--day 4-14 --invoices append`. `dupes` and `stale` go up for invoices.
   Switching back to merge only cleans keys that arrive again, so finish with step 5.
4. **Late rows:** `--day 1-12 --gl-lag-days 0 --refresh`. `missing` > 0 for `gl_entries`. Then
   `--day 1-12 --refresh` (default lag 5 days): 0 missing. Real-world takeaway: a cursor on a timestamp the source
   sets *at write time* (or an increasing sequence) is safer than one that can arrive late.
5. **Full refresh:** `--day 12 --refresh` (`refresh="drop_sources"`: drops the tables of the source and resets its
   cursors, then reloads everything). When do you need it? After bugs, after a period of `append`, or when you
   change the primary key.

Merge strategies to know about: `delete-insert` (default), `upsert`, and `scd2`. `scd2` on `customers` is a
nice bonus, but the plan is to do SCD2 with **dbt snapshots** in step 3, so use plain merge here.

## 7. Schema evolution

Run day 14, then day 15 (the schema change), and look at what dlt did:

- **New columns** (`currency`, `kid`) are added with `ALTER TABLE`. Older rows get `NULL`.
- **Nested list `lines`** becomes a **child table** `invoices__lines`, linked by `_dlt_parent_id` → `_dlt_id`
  (and `_dlt_root_id`). This is dlt's normalisation. Contrast it with Auto Loader, which keeps the struct/array as-is.
- **Type change** (`amount` as a string) becomes a **variant column** `amount__v_text` rather than a failed load.
  This is important to be able to explain: dlt never silently overwrites the type, it creates a new column.
- Compare `pipeline.default_schema.to_pretty_yaml()` before and after, or set `export_schema_path` and
  look at the git diff of the schema file. That's a very visual way to show it.
- **Schema contracts:** run day 15 again with
  `schema_contract={"tables": "evolve", "columns": "evolve", "data_type": "freeze"}`. The load now fails on the
  type change. Discuss when you'd want `freeze` (sources you own and expect to be stable) vs. `evolve` (bronze,
  where you want to capture everything) vs. `discard_row` / `discard_value`.

## 8. How dlt stores state

- **Locally:** `~/.dlt/pipelines/regnskap_databricks/` holds state, schemas, and load packages (`normalized`, `loaded`).
  Inspect it with `dlt pipeline regnskap_databricks info`, `... trace`, and `... load-package`.
- **In the destination:** three system tables in `bronze`:
  - `_dlt_loads`: one row per load (`load_id`, status, time)
  - `_dlt_pipeline_state`: compressed state, including the incremental cursors (`last_value` per resource)
  - `_dlt_version`: schema versions with their hash
- Every data row gets `_dlt_load_id` and `_dlt_id`. You can trace each row back to the load it came from.
- **Experiment:** delete `~/.dlt/pipelines/regnskap_databricks/` and run again. dlt restores state from
  `_dlt_pipeline_state`, so it continues from the right cursor. That's exactly why it works on ephemeral
  compute (a Databricks job or GitHub Actions runner) without a persistent disk.
- Read the state in code: `pipeline.state["sources"]["regnskap"]["resources"]["invoices"]["incremental"]`.

## 9. Auto Loader comparison (the reading hour plus a small hands-on)

**Reading (1 hour):** Databricks docs on Auto Loader: "What is Auto Loader", "Configure schema inference and
evolution", "File detection modes", plus a quick look at Lakeflow Declarative Pipelines and Lakeflow Connect.
Make notes on:
- `cloudFiles.format`, `cloudFiles.schemaLocation`, `cloudFiles.schemaEvolutionMode` (`addNewColumns` is the
  default and **stops the stream once** with a new column so it can restart with the new schema; also
  `rescue`, `failOnNewColumns`, `none`)
- `_rescued_data`: data that doesn't fit the schema ends up here instead of being lost
- Checkpoint plus RocksDB as the "state": which files have already been read. That's **exactly-once** per file.
- File notification mode vs. directory listing
- `trigger(availableNow=True)` for batch-like runs of a stream
- How it's used inside Lakeflow Declarative Pipelines (`read_files` / `cloudFiles` in a streaming table)

**Hands-on:** `notebooks/autoloader_compare.py` reads `landing/invoices/` into `bronze.al_invoices`.
Upload day 14, run it, upload day 15, run it again, and observe the stop-and-restart, the new columns, and
`_rescued_data`. Then note the differences from the dlt table (see the table in section 10).

## 10. Interview prep: be able to explain this

**Full vs. incremental load**
- Full: fetch everything and replace. Simple and always correct, but expensive and slow on large tables, and it
  loses history if the source deletes data. Fine for small dimensions (the chart of accounts).
- Incremental: only fetch what's new or changed since last time, using a cursor (`updated_at`, sequence, CDC).
  Cheap, but it needs state, a reliable cursor field, and handling of late rows and deletes (deletes are
  invisible to an `updated_at` cursor, so you need soft-delete flags, CDC, or a periodic full reconciliation).

**Idempotency**
- Running the same job twice gives the same result as running it once. It's the key property for safe retries
  and backfills.
- How you get it here: merge on a primary key (not append), and cursor + state that only commits after a
  successful load (dlt updates state in the same transaction as the load package).
- Auto Loader gets it from the checkpoint (each file is read once). dlt gets it from merge plus state.

**dlt and schema changes**
- Infers the schema from the data, adds new columns and tables automatically, normalises nested data into
  child tables, uses variant columns on type conflicts, versions the schema in `_dlt_version`, and can be
  locked down with schema contracts.

**Why dlt instead of Auto Loader / Lakeflow?**

| | dlt (dlthub) | Auto Loader / Lakeflow |
|---|---|---|
| Sources | Anything Python can read: REST APIs, databases, SaaS, files | Files in cloud storage (Auto Loader); Lakeflow Connect has managed connectors for a set of sources |
| Where it runs | Anywhere: laptop, Airflow, GitHub Actions, a Databricks job | Inside Databricks |
| Lock-in | Portable: same code against Snowflake/BigQuery/DuckDB | Databricks-specific |
| Local development | Run against DuckDB locally, fast feedback | Needs a cluster or serverless |
| Incremental / state | Cursor-based, state in the destination | Checkpoint per file, streaming semantics |
| Scale | Single-process Python, fine for API volumes | Spark, built for large file volumes |
| Schema | Normalises (child tables, variant columns) | Keeps the structure, `_rescued_data` |

Short answer: **Auto Loader when the data already arrives as files in the lake and the volume is large.**
**dlt when the source is an API or database without a native connector, when you want portable code, or when
you want to test locally.** They're not mutually exclusive: dlt can land files in a Volume that Auto Loader
then picks up. If they use Auto Loader, show that you understand checkpoints, schema evolution modes and
`_rescued_data`, and that you know where dlt fits better.

## 11. Order of work and checkpoints

These are **work steps**, numbered separately from the sections above. Each one names the section it implements.

1. (section 3) `.gitignore`, `ingestion/pyproject.toml` (uv), and Faker generator with tests. **Check:** the vouchers balance.
2. (section 3) `mess.py`. **Check:** you can count duplicates, nulls, and late rows in the output.
3. (section 4) Fake API with paging and clock. **Check:** `curl "localhost:8000/invoices?updated_since=..."` behaves correctly.
4. (section 5) Terraform: Volume `landing` (and staging volume if needed). **Check:** `terraform plan` only shows additions.
5. (section 5) dlt smoke test against Databricks. **Check:** a test table shows up in `bronze`.
   Tip: develop against `destination="duckdb"` first, then switch to Databricks. This also demonstrates portability.
6. (section 6) Full pipeline, then experiments 1–5. **Check:** a double run doesn't change row counts.
7. (section 7) Schema change on day 15, plus a schema contract. **Check:** you can show the child table and the variant column.
8. (section 8) The state experiment.
9. (section 9) Auto Loader reading and notebook.
10. (section 10) Write your own short answers in the README, in your own words.

**Cost:** dlt runs locally, so the only thing that costs money is the SQL warehouse while it's loading, and
it has auto-stop. Upload a small amount of data (30 days, a few thousand rows) and stop there.

**Next step (step 3):** dbt on top of bronze: dedup/cleanup in silver, snapshots (SCD2) on customers,
reconciling AR against invoices in gold, and running the dlt pipeline as a task in a Databricks job before `dbt build`.
