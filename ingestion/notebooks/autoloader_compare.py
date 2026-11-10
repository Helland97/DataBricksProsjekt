# Databricks notebook source
# MAGIC %md
# MAGIC # Auto Loader vs. dlt (step 2, section 9)
# MAGIC
# MAGIC Reads the raw invoice files in `/Volumes/lakehouse_dev/bronze/landing/invoices/` with Auto Loader into
# MAGIC `lakehouse_dev.bronze.al_invoices`, and compares the result with the dlt table `bronze.invoices`.
# MAGIC
# MAGIC **How to use it** (upload from your machine, from `ingestion/`):
# MAGIC 1. `uv run python -m pipelines.landing --day 1-14`, then **Run all** here. Auto Loader infers the v1 schema.
# MAGIC 2. `uv run python -m pipelines.landing --day 15`, then **Run all** again. New columns (`currency`, `kid`,
# MAGIC    `lines`) make the stream stop once, the restart picks up the new schema, and the string amount ends up in
# MAGIC    `_rescued_data`.
# MAGIC 3. **Run all** a third time without uploading anything: 0 new files, nothing changes (exactly-once per file).
# MAGIC
# MAGIC Start over: set the widget `reset` to `yes` and run all (drops the table and the checkpoint), then set it
# MAGIC back to `no`. Delete the uploaded files with `uv run python -m pipelines.landing --clear`.
# MAGIC
# MAGIC Runs on serverless compute: `trigger(availableNow=True)` processes what's there and stops, like a batch job.

# COMMAND ----------

dbutils.widgets.dropdown(
    "mode", "addNewColumns", ["addNewColumns", "rescue", "failOnNewColumns", "none"], "schemaEvolutionMode"
)
dbutils.widgets.dropdown("reset", "no", ["no", "yes"], "reset (drop table + checkpoint)")

MODE = dbutils.widgets.get("mode")
CATALOG, SCHEMA = "lakehouse_dev", "bronze"
VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/landing"
SOURCE = f"{VOLUME}/invoices/"

# One table and checkpoint per mode, so you can compare modes side by side without resetting.
TABLE_NAME = "al_invoices" if MODE == "addNewColumns" else f"al_invoices_{MODE.lower()}"
TARGET = f"{CATALOG}.{SCHEMA}.{TABLE_NAME}"
STATE_DIR = f"{VOLUME}/_autoloader/{TABLE_NAME}"  # outside invoices/, so Auto Loader never reads it as data
SCHEMA_LOCATION = f"{STATE_DIR}/schema"  # the inferred schema, one version per change
CHECKPOINT = f"{STATE_DIR}/checkpoint"  # which files have been read (RocksDB) + stream offsets

print(f"mode={MODE}\nsource={SOURCE}\ntarget={TARGET}\nstate={STATE_DIR}")

# COMMAND ----------

if dbutils.widgets.get("reset") == "yes":
    spark.sql(f"DROP TABLE IF EXISTS {TARGET}")
    dbutils.fs.rm(STATE_DIR, True)
    print(f"Dropped {TARGET} and deleted {STATE_DIR}. Set reset back to 'no' before the next run.")
    dbutils.notebook.exit("reset done")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run the stream
# MAGIC
# MAGIC - `cloudFiles.schemaLocation`: where Auto Loader stores the schema it inferred. Without it there's no evolution.
# MAGIC - `cloudFiles.inferColumnTypes`: infer numbers as numbers (JSON is otherwise read as strings).
# MAGIC - `cloudFiles.schemaEvolutionMode`:
# MAGIC   - `addNewColumns` (default): a new column **stops the stream once**, the schema in `schemaLocation` is
# MAGIC     updated, and the restart reads with the new schema. In a job you let a retry do the restart.
# MAGIC   - `rescue`: the schema never changes; everything new goes into `_rescued_data`. Never stops.
# MAGIC   - `failOnNewColumns`: stops, and keeps stopping until you change the schema yourself.
# MAGIC   - `none`: ignores new columns.
# MAGIC - `_rescued_data`: values that don't fit the schema (here: `"37 322,00"` in a DOUBLE column), as JSON with the
# MAGIC   file name. Nothing is lost silently.
# MAGIC - `mergeSchema` on the write side lets the Delta table accept the new columns.

# COMMAND ----------

from pyspark.sql import functions as F


def run_stream():
    stream = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", SCHEMA_LOCATION)
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", MODE)
        .option("cloudFiles.partitionColumns", "day")  # from the day=YYYY-MM-DD folders
        .load(SOURCE)
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )
    query = (
        stream.writeStream.option("checkpointLocation", CHECKPOINT)
        .option("mergeSchema", "true")
        .trigger(availableNow=True)
        .toTable(TARGET)
    )
    query.awaitTermination()
    return query


def is_new_column_stop(ex: Exception) -> bool:
    text = str(ex)
    return "UNKNOWN_FIELD" in text or "UnknownFieldException" in text


def row_count() -> int:
    return spark.table(TARGET).count() if spark.catalog.tableExists(TARGET) else 0


before = row_count()
for attempt in range(1, 4):
    try:
        run_stream()
        print(f"Attempt {attempt}: finished. {row_count() - before} new rows, {row_count()} rows in {TARGET}.")
        break
    except Exception as ex:
        if not is_new_column_stop(ex):
            raise
        print(f"Attempt {attempt}: STOPPED on new columns. The new schema is saved in {SCHEMA_LOCATION}.")
        print("   " + str(ex).splitlines()[0][:300])
        if MODE != "addNewColumns":
            print(f"   Mode {MODE} doesn't evolve by itself, so this stays stopped.")
            break
        print("   Restarting, like a job retry would...")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What Auto Loader produced

# COMMAND ----------

# The table schema: lines is kept as ARRAY<STRUCT>, the new columns are just added. Compare with dlt, which
# splits lines into the child table invoices__lines.
spark.table(TARGET).printSchema()

# COMMAND ----------

# Schema versions in schemaLocation: 0 = inferred from days 1-14, 1 = after the new columns on day 15.
display(dbutils.fs.ls(f"{SCHEMA_LOCATION}/_schemas"))

# COMMAND ----------

# Rows that didn't fit: the Norwegian string amount ends up here instead of failing or being lost.
display(
    spark.sql(f"""
    SELECT day, invoice_id, amount_incl_vat, _rescued_data
    FROM {TARGET}
    WHERE _rescued_data IS NOT NULL
    ORDER BY day, invoice_id
""")
)

# COMMAND ----------

# The checkpoint as "state": every file Auto Loader has read. A file is read exactly once, so running again
# without new files adds nothing.
display(spark.sql(f"SELECT path, size, commit_time FROM cloud_files_state('{CHECKPOINT}') ORDER BY path"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compare with the dlt table
# MAGIC
# MAGIC Load dlt to the same day first, from `ingestion/`:
# MAGIC `uv run python -m pipelines.regnskap --day 1-15 --refresh --destination databricks`
# MAGIC (with the fake API running).

# COMMAND ----------

# Auto Loader appends every row of every file: all versions of an invoice and the duplicates.
# dlt merges on invoice_id, so it has one row per invoice, in its latest version.
display(
    spark.sql(f"""
    SELECT 'auto loader' AS loader, count(*) AS rows, count(DISTINCT invoice_id) AS invoices FROM {TARGET}
    UNION ALL
    SELECT 'dlt', count(*), count(DISTINCT invoice_id) FROM {CATALOG}.{SCHEMA}.invoices
""")
)

# COMMAND ----------

# The type change: Auto Loader keeps the column type and rescues the value, dlt adds a variant column.
display(
    spark.sql(f"""
    SELECT invoice_id, amount_incl_vat, amount_incl_vat__v_text
    FROM {CATALOG}.{SCHEMA}.invoices
    WHERE amount_incl_vat__v_text IS NOT NULL
""")
)

# COMMAND ----------

# The nested list: same data, two shapes. Auto Loader keeps lines as an array on the invoice row ...
display(
    spark.sql(f"""
    SELECT invoice_id, line.line_no, line.description, line.amount_ex_vat
    FROM {TARGET} LATERAL VIEW explode(lines) AS line
    WHERE lines IS NOT NULL
    ORDER BY invoice_id, line.line_no
    LIMIT 10
""")
)

# COMMAND ----------

# ... while dlt has a separate table, joined through _dlt_parent_id -> _dlt_id.
display(
    spark.sql(f"""
    SELECT i.invoice_id, l.line_no, l.description, l.amount_ex_vat
    FROM {CATALOG}.{SCHEMA}.invoices i
    JOIN {CATALOG}.{SCHEMA}.invoices__lines l ON l._dlt_parent_id = i._dlt_id
    ORDER BY i.invoice_id, l.line_no
    LIMIT 10
""")
)

# COMMAND ----------

# With Auto Loader, dedup to the latest version is your job, typically in silver (step 3: dbt).
display(
    spark.sql(f"""
    SELECT count(*) AS invoices_latest_version FROM (
        SELECT *, row_number() OVER (PARTITION BY invoice_id ORDER BY updated_at DESC) AS rn
        FROM {TARGET}
    ) WHERE rn = 1
""")
)
