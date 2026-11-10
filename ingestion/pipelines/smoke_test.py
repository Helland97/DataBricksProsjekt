"""Smoke test: prove that dlt can reach a destination before writing the real pipeline.

    uv run python -m pipelines.smoke_test                         # local DuckDB file, no cloud
    uv run python -m pipelines.smoke_test --destination databricks
    uv run python -m pipelines.smoke_test --destination databricks --cleanup

Loads three hard-coded rows into <dataset>.smoke_test and reads them back. The pipeline code is
identical for both destinations, only the `destination` argument changes. That's dlt's portability.

On Databricks this checks the whole chain: auth (az login, see pipelines/databricks_setup.py), the SQL warehouse
(http_path), the catalog, write access to bronze, and the staging volume (PUT + COPY INTO).
--cleanup drops the smoke_test table again. The _dlt_* system tables are left alone: they're shared
with the real pipelines in the same dataset.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import dlt

from pipelines.databricks_setup import destination


@dlt.resource(name="smoke_test", write_disposition="replace", primary_key="id")
def smoke_rows():
    now = datetime.now(timezone.utc)
    yield [
        {"id": 1, "message": "hei fra dlt", "loaded_at": now},
        {"id": 2, "message": "æøå survives the trip", "loaded_at": now},
        {"id": 3, "message": None, "loaded_at": now},
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--destination", choices=["duckdb", "databricks"], default="duckdb")
    p.add_argument("--cleanup", action="store_true", help="drop the smoke_test table afterwards")
    args = p.parse_args()

    pipeline = dlt.pipeline(
        pipeline_name=f"smoke_test_{args.destination}",
        destination=destination(args.destination),
        dataset_name="bronze",
    )
    info = pipeline.run(smoke_rows())
    print(info)

    with pipeline.sql_client() as client:
        table = client.make_qualified_table_name("smoke_test")
        rows = client.execute_sql(f"SELECT id, message FROM {table} ORDER BY id")
        print(f"\nRead back from {table}:")
        for row in rows:
            print("  ", tuple(row))
        assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"

        if args.cleanup:
            client.execute_sql(f"DROP TABLE IF EXISTS {table}")
            print(f"Dropped {table}")

    print("\nSmoke test OK")


if __name__ == "__main__":
    main()
