"""Daily load for the Databricks job regnskap-daily (task `ingest`). STEG5-PLAN.md section 4.

The job runs this file as a script (spark_python_task), straight from the Git checkout:
    python ingestion/jobs/daily_load.py --day auto --run-date 2026-12-01 --server-hostname ... --http-path ...

Run it locally the same way (as a script, not with -m), from the repo root:
    uv run --directory ingestion python jobs/daily_load.py --destination duckdb --day 3
    uv run --directory ingestion python jobs/daily_load.py --destination databricks --day 30

The job has no fake API to call, so the script brings its own source. It generates the dataset
(deterministic: same seed, same data) into a temp folder, serves it on 127.0.0.1 in a background thread,
loads one simulated day, and then checks bronze against the source. Any difference fails the task.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


def _this_file() -> str:
    """The path of this file. A Databricks spark_python_task doesn't run the file as a script: it reads it
    and calls exec(compile(code, filename, "exec")), so __file__ is never set (NameError on the first run,
    STEG5 section 6.4). compile() does store the path in the code object, so read it from there instead."""
    try:
        return __file__
    except NameError:
        return sys._getframe(1).f_code.co_filename


# Run as a script, Python only puts jobs/ on sys.path. The modules below live in ingestion/.
INGESTION_DIR = Path(_this_file()).resolve().parents[1]
sys.path.insert(0, str(INGESTION_DIR))

import dlt
from fake_api.serve import serve
from generator.generate import write_dataset
from pipelines.databricks_setup import destination, running_on_databricks
from pipelines.regnskap import compare, run_days

# The generated dataset. Must match what bronze was loaded from (generator defaults).
SIM_START = date(2026, 10, 1)
SIM_DAYS = 30
SEED = 42
# The real date that counts as simulated day 1 for --day auto. Set it when the schedule is turned on (section 6).
SIM_ANCHOR = date(2026, 12, 1)


class BronzeMismatch(Exception):
    """Bronze doesn't match the source after the load. Fails the task."""


def resolve_day(day: str, run_date: date, anchor: date = SIM_ANCHOR, max_day: int = SIM_DAYS) -> int:
    """Which simulated day to load.

    'auto': days since the anchor + 1, clamped to 1..max_day. Before the anchor = day 1, after the last day
    the clock stays on max_day (every run then loads nothing new, which is fine: the load is idempotent).
    A number: exactly that day (manual runs, demos, backfills).
    """
    if day == "auto":
        return min(max((run_date - anchor).days + 1, 1), max_day)
    n = int(day)
    if not 1 <= n <= max_day:
        raise ValueError(f"--day must be 'auto' or a number from 1 to {max_day}, got {day!r}")
    return n


def check_bronze(stats: dict[str, dict]) -> None:
    """After a load, every table must match the source: nothing missing, no duplicates, no stale versions."""
    bad = {t: s for t, s in stats.items() if s["missing"] or s["duplicates"] or s["stale"]}
    if bad:
        details = "\n".join(
            f"  {t}: missing={s['missing']} duplicates={s['duplicates']} stale={s['stale']}" for t, s in bad.items()
        )
        raise BronzeMismatch(f"Bronze doesn't match the source after the load:\n{details}")


def sim_date(day: int) -> date:
    """The calendar date of a simulated day in the generated data (day 1 = SIM_START)."""
    return SIM_START + timedelta(days=day - 1)


def publish_sim_date(day: int) -> None:
    """Hand the simulated date to the next task (dbt_build) as a Databricks task value. dbt uses it as
    snapshot_time, so check-strategy snapshot versions get the simulated date and not the real run date.
    Does nothing outside Databricks: there are no task values on a laptop."""
    if not running_on_databricks():
        return
    from databricks.sdk.runtime import dbutils  # only exists on Databricks compute

    dbutils.jobs.taskValues.set(key="sim_date", value=sim_date(day).isoformat())


def configure_databricks(server_hostname: str | None, http_path: str | None) -> None:
    """In the job, the warehouse comes from task parameters, because there's no secrets.toml there.
    dlt reads its config from these environment variables. Locally, leave both out and secrets.toml is used."""
    if server_hostname:
        os.environ["DESTINATION__DATABRICKS__CREDENTIALS__SERVER_HOSTNAME"] = server_hostname
    if http_path:
        os.environ["DESTINATION__DATABRICKS__CREDENTIALS__HTTP_PATH"] = http_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", default="auto", help="'auto' (from --run-date) or a simulated day 1..30")
    p.add_argument("--run-date", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD, for --day auto")
    p.add_argument("--destination", choices=["databricks", "duckdb"], default="databricks")
    p.add_argument("--server-hostname", help="Databricks workspace host, without https://")
    p.add_argument("--http-path", help="SQL warehouse HTTP path")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    os.chdir(INGESTION_DIR)  # dlt reads .dlt/config.toml from the current directory
    if args.destination == "databricks":
        configure_databricks(args.server_hostname, args.http_path)
    day = resolve_day(args.day, args.run_date)
    print(f"Run date {args.run_date}: loading simulated day {day} into {args.destination}")

    # Short folder names: dlt's load paths are deep, and Windows has a 260-character path limit.
    with tempfile.TemporaryDirectory(prefix="dl", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "d"
        write_dataset(data_dir, SIM_START, SIM_DAYS, SEED)
        with serve(data_dir) as api_url:
            pipeline = dlt.pipeline(
                pipeline_name=f"regnskap_{args.destination}",  # same name as local runs = the same state in bronze
                destination=destination(args.destination),
                dataset_name="bronze",
                pipelines_dir=str(Path(tmp) / "p"),  # empty every run: dlt restores its state from the destination
            )
            run_days(pipeline, api_url, [day])  # raises ContractViolation if a schema contract stops the load
            stats = compare(pipeline, api_url)
    check_bronze(stats)
    publish_sim_date(day)
    print(f"Bronze matches the source. Simulated date for dbt: {sim_date(day)}")

    print("Bronze matches the source.")


if __name__ == "__main__":
    main()
