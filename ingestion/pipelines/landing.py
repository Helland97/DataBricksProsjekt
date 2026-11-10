"""Upload generated JSONL files to the `landing` volume, one day at a time, for the Auto Loader notebook.

This is the "files arrive in the lake" side of section 9. dlt isn't involved: the files go up exactly as the
generator wrote them, mess included, and Auto Loader (notebooks/autoloader_compare.py) picks them up.

    uv run python -m pipelines.landing --day 1-14     # invoices for days 1-14
    uv run python -m pipelines.landing --day 15       # then the day with the schema change
    uv run python -m pipelines.landing --list         # what's in the volume now
    uv run python -m pipelines.landing --clear        # delete the uploaded files (start over)

Login is the same as for the dlt pipeline: `az login`, plus server_hostname from .dlt/secrets.toml.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

from pipelines.databricks_setup import use_azure_cli_auth
from pipelines.regnskap import parse_days

VOLUME = "/Volumes/lakehouse_dev/bronze/landing"
DATA_DIR = Path("data")


def day_to_date(day: int) -> date:
    manifest = json.loads((DATA_DIR / "_manifest.json").read_text(encoding="utf-8"))
    return date.fromisoformat(manifest["start"]) + timedelta(days=day - 1)


def client() -> WorkspaceClient:
    use_azure_cli_auth()
    return WorkspaceClient()


def upload(w: WorkspaceClient, entity: str, days: list[int]) -> None:
    for day in days:
        partition = f"day={day_to_date(day).isoformat()}"
        local = DATA_DIR / entity / partition / "part-0.jsonl"
        if not local.exists():
            print(f"day {day:>2}  {partition}  no {entity} that day, skipped")
            continue
        remote = f"{VOLUME}/{entity}/{partition}/part-0.jsonl"
        with local.open("rb") as f:
            w.files.upload(remote, f, overwrite=True)
        rows = len(local.read_text(encoding="utf-8").splitlines())
        print(f"day {day:>2}  {partition}  {rows:>4} rows -> {remote}")


def files(w: WorkspaceClient, directory: str):
    try:
        for entry in w.files.list_directory_contents(directory):
            if entry.is_directory:
                yield from files(w, entry.path)
            else:
                yield entry
    except NotFound:
        return


def list_files(w: WorkspaceClient, entity: str) -> None:
    found = sorted(files(w, f"{VOLUME}/{entity}"), key=lambda e: e.path)
    for e in found:
        print(f"{e.file_size:>8} bytes  {e.path}")
    print(f"{len(found)} file(s) in {VOLUME}/{entity}")


def clear(w: WorkspaceClient, entity: str) -> None:
    root = f"{VOLUME}/{entity}"
    found = list(files(w, root))
    for e in found:
        w.files.delete(e.path)
    # Directories must be empty before they can be deleted, so the deepest go first.
    dirs = sorted({str(Path(e.path).parent).replace("\\", "/") for e in found}, key=len, reverse=True)
    for d in [*dirs, root]:
        try:
            w.files.delete_directory(d)
        except NotFound:
            pass
    print(f"Deleted {len(found)} file(s) from {root}. Reset the notebook too (widget reset = yes).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=parse_days, help="day N or a range like 1-14")
    p.add_argument("--entity", default="invoices", choices=["accounts", "customers", "invoices", "gl_entries"])
    p.add_argument("--list", action="store_true", help="list the files in the volume")
    p.add_argument("--clear", action="store_true", help="delete the uploaded files for the entity")
    args = p.parse_args()
    if not (args.day or args.list or args.clear):
        p.error("give --day, --list or --clear")

    w = client()
    if args.clear:
        clear(w, args.entity)
    if args.day:
        upload(w, args.entity, args.day)
    if args.list:
        list_files(w, args.entity)


if __name__ == "__main__":
    main()
