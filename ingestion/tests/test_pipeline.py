"""End-to-end: generated data -> fake API (real HTTP, in a thread) -> dlt -> DuckDB.

These are the section 6 experiments as tests. Everything runs in a temp folder: its own data,
its own DuckDB file and its own dlt pipelines_dir, so ~/.dlt and ingestion/data are not touched.
"""

import json
import socket
import threading
import time
from datetime import date, timedelta

import dlt
import pytest
import uvicorn

from fake_api.app import create_app
from generator import mess
from generator.generate import generate, write
from pipelines.regnskap import compare, run_days

START = date(2026, 10, 1)
DAYS = 30


@pytest.fixture(scope="module")
def api_url(tmp_path_factory):
    out = tmp_path_factory.mktemp("pipe") / "data"
    data = generate(START, DAYS, seed=42)
    mess.apply(data, list(mess.MESS_STEPS), seed=42, start=START, end=START + timedelta(days=DAYS - 1))
    write(data, out)
    (out / "_manifest.json").write_text(json.dumps({"start": START.isoformat(), "days": DAYS}))

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(out, day=1), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def pipeline(tmp_path):
    # Short name: dlt's load package paths are deep, and Windows has a 260-character path limit.
    return dlt.pipeline(
        pipeline_name="t",
        # File name must differ from the dataset name, or DuckDB can't tell catalog "bronze" from schema "bronze".
        destination=dlt.destinations.duckdb(str(tmp_path / "lakehouse.duckdb")),
        dataset_name="bronze",
        pipelines_dir=str(tmp_path / "pipelines"),
    )


def all_clean(stats):
    return all(s["missing"] == s["duplicates"] == s["stale"] == 0 for s in stats.values())


def test_day_by_day_matches_source(pipeline, api_url):
    """Experiment 1: three daily loads, bronze has every key once, in its latest version."""
    run_days(pipeline, api_url, [1, 2, 3], verbose=False)
    stats = compare(pipeline, api_url)
    assert all_clean(stats), stats
    # The source really has duplicates, merge removed them.
    assert stats["gl_entries"]["source_rows"] > stats["gl_entries"]["source_keys"]


def test_rerun_same_day_is_idempotent(pipeline, api_url):
    """Experiment 2: loading the same day again changes nothing."""
    run_days(pipeline, api_url, [1, 2, 3, 4, 5], verbose=False)
    before = compare(pipeline, api_url)
    run_days(pipeline, api_url, [5, 5], verbose=False)
    assert compare(pipeline, api_url) == before
    assert all_clean(before)


def test_append_creates_duplicates_and_refresh_fixes_it(pipeline, api_url):
    """Experiments 3 and 5: append keeps every version, a full refresh with merge cleans it up."""
    run_days(pipeline, api_url, list(range(1, 15)), invoices="append", verbose=False)
    stats = compare(pipeline, api_url)["invoices"]
    assert stats["duplicates"] > 0 and stats["stale"] > 0

    run_days(pipeline, api_url, [14], refresh=True, verbose=False)
    assert all_clean(compare(pipeline, api_url))


def test_late_rows_are_lost_without_lag(pipeline, api_url):
    """Experiment 4a: a plain cursor on updated_at misses rows that arrive late."""
    run_days(pipeline, api_url, list(range(1, 13)), gl_lag_days=0, verbose=False)
    assert compare(pipeline, api_url)["gl_entries"]["missing"] > 0


def test_lag_catches_late_rows(pipeline, api_url):
    """Experiment 4b: with lag >= the longest delay (5 days) nothing is lost, and merge makes the overlap harmless."""
    run_days(pipeline, api_url, list(range(1, 13)), gl_lag_days=5, verbose=False)
    assert all_clean(compare(pipeline, api_url))


def test_handwritten_resource_behaves_the_same(pipeline, api_url):
    run_days(pipeline, api_url, list(range(1, 13)), handwritten_gl=True, verbose=False)
    assert all_clean(compare(pipeline, api_url))
