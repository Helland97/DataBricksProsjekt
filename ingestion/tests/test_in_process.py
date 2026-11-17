"""The Databricks job's path to the API (fake_api/in_process.py) must work without any network.

Serverless compute refuses connections to 127.0.0.1 (STEG5 section 6.4). These tests block every socket
connection the same way, then load and compare through in_process(). If anything in that path tried to
open a connection (dlt's REST client, the clock, the comparison), the test would fail.
"""

import socket
from datetime import date

import dlt
import pytest
from fake_api.in_process import in_process
from generator.generate import write_dataset
from pipelines.regnskap import SESSION, compare, run_days

START = date(2026, 10, 1)
DAYS = 30


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("inproc") / "data"
    write_dataset(out, START, DAYS, seed=42)
    return out


@pytest.fixture()
def no_network(monkeypatch):
    """Like serverless: every attempt to connect a socket is refused."""

    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair

    def refuse(self, address):
        raise ConnectionRefusedError(111, f"Connection refused (blocked by test): {address}")

    def socketpair(*args, **kwargs):
        # asyncio uses socketpair() as an internal pipe in the same process. Windows has no real one, so
        # Python fakes it with a TCP connection to 127.0.0.1. On Linux (Databricks, CI) it's a Unix pipe and
        # never touches TCP. Allow it, so the test behaves the same on every OS.
        with monkeypatch.context() as m:
            m.setattr(socket.socket, "connect", real_connect)
            return real_socketpair(*args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: 111)
    monkeypatch.setenv("RUNTIME__DLTHUB_TELEMETRY", "false")  # dlt's telemetry would try the network


@pytest.fixture()
def pipeline(tmp_path):
    return dlt.pipeline(
        pipeline_name="ip",
        destination=dlt.destinations.duckdb(str(tmp_path / "lakehouse.duckdb")),
        dataset_name="bronze",
        pipelines_dir=str(tmp_path / "pipelines"),
    )


def test_the_block_really_blocks(no_network):
    """Guard for the test itself: without this, a broken monkeypatch would make the next test pass for nothing."""
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_load_and_compare_without_network(no_network, data_dir, pipeline):
    with in_process(data_dir, SESSION) as api_url:
        run_days(pipeline, api_url, [1, 2, 3], verbose=False)
        stats = compare(pipeline, api_url)
    assert all(s["missing"] == s["duplicates"] == s["stale"] == 0 for s in stats.values()), stats
    assert stats["invoices"]["bronze_keys"] > 0


def test_adapter_is_removed_afterwards(data_dir):
    with in_process(data_dir, SESSION) as api_url:
        assert api_url in SESSION.adapters
    assert api_url not in SESSION.adapters
