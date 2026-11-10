"""The real pipeline: fake ERP API -> dlt -> bronze. Section 6 of STEG2-PLAN.md.

Start the API first (in its own terminal, from ingestion/):
    uv run uvicorn fake_api.app:app

Then, from ingestion/:
    uv run python -m pipelines.regnskap --day 1                      # set the API clock to day 1 and load
    uv run python -m pipelines.regnskap --day 1-3                    # day 1, 2 and 3, one load per day
    uv run python -m pipelines.regnskap --day 3                      # same day again: nothing changes
    uv run python -m pipelines.regnskap --day 4 --invoices append    # experiment 3: duplicates
    uv run python -m pipelines.regnskap --day 1-12 --gl-lag-days 0   # experiment 4: late rows get lost
    uv run python -m pipelines.regnskap --day 12 --refresh           # experiment 5: drop everything, reload
    uv run python -m pipelines.regnskap --day 5 --handwritten-gl     # gl_entries via the hand-written resource
    uv run python -m pipelines.regnskap --report                     # only print the comparison table
    uv run python -m pipelines.regnskap --report --schema            # ... plus tables, columns, variants
    uv run python -m pipelines.regnskap --day 15 --contract freeze-types   # section 7: schema contracts

Add --destination databricks to load into lakehouse_dev.bronze instead of a local DuckDB file
(ingestion/regnskap_duckdb.duckdb). The two destinations have separate pipeline state.

Every schema change is exported as YAML to schemas/<destination>/regnskap.schema.yaml, so you can
git diff the schema before and after the day 15 schema change.

After every load, a table compares bronze against the source (the API at the current clock day):
rows, distinct keys, keys missing in bronze, and rows that hold an outdated version.

Strategy per resource:
    accounts    replace                     no updated_at, so a full load is the only option
    customers   merge on customer_id        incremental on updated_at, latest version wins
    invoices    merge on invoice_id         incremental on updated_at, latest version wins
    gl_entries  merge on entry_id           incremental on updated_at, with lag for late rows
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import dlt
import requests  # plain requests for the clock and the comparison: fail fast if the API is down
from dlt.sources.helpers import requests as dlt_requests  # retrying client, for actual loading
from dlt.common.schema.exceptions import DataValidationError
from dlt.sources.rest_api import rest_api_resources

from pipelines.databricks_setup import destination as make_destination

DEFAULT_API_URL = "http://127.0.0.1:8000"
INITIAL_CURSOR = "2000-01-01T00:00:00Z"  # first run: fetch everything
DAY = 24 * 3600  # lag on a datetime cursor is in seconds

# resource -> (API path, primary key)
ENTITIES = {
    "accounts": ("accounts", "account_no"),
    "customers": ("customers", "customer_id"),
    "invoices": ("invoices", "invoice_id"),
    "gl_entries": ("gl-entries", "entry_id"),
}


# --- Source ---------------------------------------------------------------------


def incremental(lag_days: float = 0) -> dict:
    """Declarative incremental: dlt sends its saved cursor as ?updated_since=..., minus the lag."""
    cfg: dict[str, Any] = {
        "start_param": "updated_since",
        "cursor_path": "updated_at",
        "initial_value": INITIAL_CURSOR,
    }
    if lag_days:
        cfg["lag"] = lag_days * DAY
    return cfg


MERGE = {"disposition": "merge", "strategy": "delete-insert"}
# Several versions of the same key can arrive in one load (e.g. sent + paid). Keep the newest.
LATEST_WINS = {"updated_at": {"dedup_sort": "desc"}}


def rest_api_config(api_url: str, invoices_disposition: str, gl_lag_days: float, include_gl: bool) -> dict:
    resources: list[dict] = [
        {
            "name": "accounts",
            "write_disposition": "replace",
            "primary_key": "account_no",
            "endpoint": {"path": "accounts", "paginator": "single_page"},
        },
        {
            "name": "customers",
            "write_disposition": MERGE,
            "primary_key": "customer_id",
            "columns": LATEST_WINS,
            "endpoint": {"path": "customers", "params": {"page_size": 100}, "incremental": incremental()},
        },
        {
            "name": "invoices",
            "write_disposition": MERGE if invoices_disposition == "merge" else "append",
            "primary_key": "invoice_id",
            "columns": LATEST_WINS,
            "endpoint": {"path": "invoices", "params": {"page_size": 100}, "incremental": incremental()},
        },
    ]
    if include_gl:
        resources.append(
            {
                "name": "gl_entries",
                "write_disposition": MERGE,
                "primary_key": "entry_id",
                "endpoint": {
                    "path": "gl-entries",
                    "params": {"page_size": 100},
                    "incremental": incremental(gl_lag_days),
                },
            }
        )
    return {
        "client": {
            "base_url": api_url,
            "paginator": {"type": "page_number", "base_page": 1, "page_param": "page", "total_path": "total_pages"},
        },
        "resource_defaults": {"endpoint": {"data_selector": "data"}},
        "resources": resources,
    }


@dlt.resource(name="gl_entries", write_disposition=MERGE, primary_key="entry_id")
def gl_entries_handwritten(
    api_url: str,
    updated_at: dlt.sources.incremental[str] = dlt.sources.incremental("updated_at", initial_value=INITIAL_CURSOR),
):
    """The same as the declarative gl_entries resource, written by hand.

    `updated_at.last_value` is the cursor dlt saved after the previous run, already minus the lag.
    It's sent to the API as updated_since; dlt then also filters rows below it and deduplicates rows
    exactly on it. After the run, dlt stores the highest updated_at it saw as the new cursor.

    Read the cursor ONCE, before paging. `last_value` moves forward as soon as rows pass through, so
    reading it inside the loop sends a different updated_since for page 2, the result set shifts under
    the page numbers, and rows are skipped. (This bug was in the first version of this function.)
    """
    since = updated_at.last_value
    page: int | None = 1
    while page is not None:
        resp = dlt_requests.get(
            f"{api_url}/gl-entries",
            params={"updated_since": since, "page": page, "page_size": 100},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        yield body["data"]
        page = body["next_page"]


@dlt.source(name="regnskap")
def regnskap(
    api_url: str = DEFAULT_API_URL,
    invoices_disposition: str = "merge",
    gl_lag_days: float = 5,
    handwritten_gl: bool = False,
):
    """All four resources. State (cursors) is stored under the source name `regnskap`, so the
    declarative and the hand-written gl_entries share the same cursor and can be swapped freely."""
    config = rest_api_config(api_url, invoices_disposition, gl_lag_days, include_gl=not handwritten_gl)
    resources = rest_api_resources(config)
    if handwritten_gl:
        lag = {"lag": gl_lag_days * DAY} if gl_lag_days else {}
        resources.append(
            gl_entries_handwritten(
                api_url,
                updated_at=dlt.sources.incremental("updated_at", initial_value=INITIAL_CURSOR, **lag),
            )
        )
    return resources


# --- Clock and comparison --------------------------------------------------------


def set_clock(api_url: str, day: int) -> dict:
    resp = requests.post(f"{api_url}/_clock", json={"day": day}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_all(api_url: str, path: str) -> list[dict]:
    if path == "accounts":
        return requests.get(f"{api_url}/accounts", timeout=30).json()["data"]
    rows, page = [], 1
    while page is not None:
        body = requests.get(f"{api_url}/{path}", params={"page": page, "page_size": 1000}, timeout=30).json()
        rows += body["data"]
        page = body["next_page"]
    return rows


def as_cursor(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def compare(pipeline: dlt.Pipeline, api_url: str) -> dict[str, dict]:
    """Bronze vs. source for every resource. This is what the experiments look at."""
    result = {}
    with pipeline.sql_client() as client:
        for name, (path, key) in ENTITIES.items():
            source_rows = fetch_all(api_url, path)
            latest: dict[str, str] = {}
            for r in source_rows:
                k = str(r[key])
                latest[k] = max(latest.get(k, ""), r.get("updated_at", ""))

            table = client.make_qualified_table_name(name)
            has_cursor = name != "accounts"
            cols = f"{key}, updated_at" if has_cursor else key
            try:
                bronze = client.execute_sql(f"SELECT {cols} FROM {table}")
            except Exception:
                bronze = []  # table doesn't exist yet
            bronze_keys = {str(r[0]) for r in bronze}
            stale = sum(1 for r in bronze if has_cursor and as_cursor(r[1]) != latest.get(str(r[0])))
            result[name] = {
                "source_rows": len(source_rows),
                "source_keys": len(latest),
                "bronze_rows": len(bronze),
                "bronze_keys": len(bronze_keys),
                "missing": len(set(latest) - bronze_keys),
                "duplicates": len(bronze) - len(bronze_keys),
                "stale": stale,
            }
    return result


def cursors(pipeline: dlt.Pipeline) -> dict[str, str]:
    resources = pipeline.state.get("sources", {}).get("regnskap", {}).get("resources", {})
    return {
        name: r.get("incremental", {}).get("updated_at", {}).get("last_value", "-")
        for name, r in resources.items()
    }


def print_report(pipeline: dlt.Pipeline, api_url: str) -> dict[str, dict]:
    stats = compare(pipeline, api_url)
    cur = cursors(pipeline)
    header = f"{'table':<11} {'source':>7} {'keys':>6} │ {'bronze':>7} {'keys':>6} {'missing':>8} {'dupes':>6} {'stale':>6}  cursor (last_value)"
    print(header)
    print("─" * len(header))
    for name, s in stats.items():
        flag = "" if s["missing"] == s["duplicates"] == s["stale"] == 0 else "  ◄"
        print(
            f"{name:<11} {s['source_rows']:>7} {s['source_keys']:>6} │ {s['bronze_rows']:>7} {s['bronze_keys']:>6}"
            f" {s['missing']:>8} {s['duplicates']:>6} {s['stale']:>6}  {cur.get(name, '-')}{flag}"
        )
    print(
        "source = rows the API has sent up to today (incl. duplicates and old versions), keys = distinct keys.\n"
        "missing = keys in the source but not in bronze. stale = bronze rows holding an outdated version."
    )
    return stats


# --- Schema contracts and schema overview (section 7) -----------------------------

# --contract name -> dlt schema_contract. Entities: tables (new tables), columns (new columns),
# data_type (a value that doesn't fit the column's type, which would otherwise create a variant column).
CONTRACTS: dict[str, Any] = {
    "evolve": None,  # dlt's default: accept every change
    "freeze-types": {"data_type": "freeze"},  # new tables/columns OK, type changes stop the load
    "freeze-columns": {"columns": "freeze", "data_type": "freeze"},  # also stop on new columns
    "freeze": "freeze",  # nothing may change, not even a new (child) table
    "discard-value": {"columns": "discard_value", "data_type": "discard_value"},  # drop only the bad value
    "discard-row": {"columns": "discard_row", "data_type": "discard_row"},  # drop the whole row
}


class ContractViolation(Exception):
    """A schema contract stopped the load. The rejected package has been aborted."""


def find_validation_error(ex: BaseException | None) -> DataValidationError | None:
    while ex is not None:
        if isinstance(ex, DataValidationError):
            return ex
        ex = getattr(ex, "exception", None) or ex.__cause__
    return None


def print_schema(pipeline: dlt.Pipeline) -> None:
    """Tables, child tables, columns and variant columns, with how many rows have a value."""
    schema = pipeline.default_schema
    print("\nSchema (columns as name:type filled/total, \u25b2 = variant column from a type change)")
    with pipeline.sql_client() as client:
        for table in schema.data_tables():
            name = table["name"]
            columns = schema.get_table_columns(name)
            cols = [c for c in columns if not c.startswith("_dlt")]
            qualified = client.make_qualified_table_name(name)
            counts = ", ".join(f"count({client.escape_column_name(c)})" for c in cols)
            try:
                total, *filled = client.execute_sql(f"SELECT count(*), {counts} FROM {qualified}")[0]
            except Exception:
                total, filled = 0, [0] * len(cols)
            parent = f"  (child of {table['parent']}, linked by _dlt_parent_id)" if table.get("parent") else ""
            print(f"  {name}: {total} rows{parent}")
            for col, n in zip(cols, filled):
                mark = " \u25b2" if "__v_" in col else ""
                print(f"      {col}:{columns[col].get('data_type')} {n}/{total}{mark}")


# --- CLI -----------------------------------------------------------------------------


def parse_days(text: str) -> list[int]:
    if "-" in text:
        first, last = (int(x) for x in text.split("-"))
        return list(range(first, last + 1))
    return [int(text)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--destination", choices=["duckdb", "databricks"], default="duckdb")
    p.add_argument("--day", type=parse_days, help="set the API clock first: '5' or a range '1-3'")
    p.add_argument("--invoices", choices=["merge", "append"], default="merge", help="write disposition for invoices")
    p.add_argument("--gl-lag-days", type=float, default=5, help="lag on the gl_entries cursor, 0 = no lag")
    p.add_argument("--handwritten-gl", action="store_true", help="load gl_entries with the hand-written resource")
    p.add_argument("--refresh", action="store_true", help="drop all regnskap tables and state, then reload")
    p.add_argument("--report", action="store_true", help="don't load, only print the comparison")
    p.add_argument("--schema", action="store_true", help="also print tables, columns and variant columns")
    p.add_argument("--contract", choices=list(CONTRACTS), default="evolve", help="schema contract for this run")
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    args = p.parse_args()

    try:
        clock = requests.get(f"{args.api_url}/_clock", timeout=5).json()
    except requests.exceptions.ConnectionError:
        raise SystemExit(f"The fake API isn't running on {args.api_url}. Start it: uv run uvicorn fake_api.app:app")

    pipeline = dlt.pipeline(
        pipeline_name=f"regnskap_{args.destination}",
        destination=make_destination(args.destination),
        dataset_name="bronze",
        # Write the schema as YAML after every change: git diff it to see schema evolution.
        export_schema_path=f"schemas/{args.destination}",
    )

    if args.report:
        print(f"API clock: day {clock['day']} ({clock['date']})")
    else:
        try:
            run_days(
                pipeline,
                args.api_url,
                args.day or [None],
                invoices=args.invoices,
                gl_lag_days=args.gl_lag_days,
                handwritten_gl=args.handwritten_gl,
                refresh=args.refresh,
                contract=args.contract,
            )
        except ContractViolation as ex:
            print(f"\nSTOPPED: {ex}")
    print()
    print_report(pipeline, args.api_url)
    if args.schema:
        print_schema(pipeline)


def run_days(
    pipeline: dlt.Pipeline,
    api_url: str,
    days: list[int | None],
    invoices: str = "merge",
    gl_lag_days: float = 5,
    handwritten_gl: bool = False,
    refresh: bool = False,
    contract: str = "evolve",
    verbose: bool = True,
) -> None:
    """One load per day. A day of None means: don't touch the clock, load whatever is there now.

    Raises ContractViolation if the schema contract rejects the data. The rejected package is aborted
    first: otherwise it stays pending, and dlt retries it on the next run *instead of* extracting new data.
    """
    for i, day in enumerate(days):
        clock = set_clock(api_url, day) if day is not None else requests.get(f"{api_url}/_clock", timeout=5).json()
        source = regnskap(
            api_url=api_url,
            invoices_disposition=invoices,
            gl_lag_days=gl_lag_days,
            handwritten_gl=handwritten_gl,
        )
        # drop_sources: drop the tables of this source and reset its state (cursors) before loading.
        try:
            info = pipeline.run(
                source,
                refresh="drop_sources" if refresh and i == 0 else None,
                schema_contract=CONTRACTS[contract],
            )
        except Exception as ex:
            violation = find_validation_error(ex)
            if violation is None:
                raise
            pipeline.abort_packages()  # also restores local state (cursors, schema) to before this package
            raise ContractViolation(
                f"Day {clock['day']}: schema contract '{contract}' rejected the data. "
                f"Table `{violation.table_name}`, column `{violation.column_name}`: "
                f"`{violation.schema_entity}` is set to `{violation.contract_mode}`. "
                "Nothing from this run was loaded."
            ) from ex
        if verbose:
            received = {
                t: n for t, n in pipeline.last_trace.last_normalize_info.row_counts.items() if not t.startswith("_dlt")
            }
            load_id = info.loads_ids[-1] if info.loads_ids else "-"
            print(f"\n=== Day {clock['day']} ({clock['date']}) → {pipeline.destination.destination_name} ===")
            print(f"load {load_id}: rows received {received or '{}'}")


if __name__ == "__main__":
    main()
