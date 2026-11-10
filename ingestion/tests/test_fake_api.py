import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fake_api.app import create_app
from generator import mess
from generator.generate import generate, write

START = date(2026, 10, 1)
DAYS = 30
END = START + timedelta(days=DAYS - 1)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("api") / "data"
    data = generate(START, DAYS, seed=42)
    mess.apply(data, list(mess.MESS_STEPS), seed=42, start=START, end=END)
    write(data, out)
    (out / "_manifest.json").write_text(json.dumps({"start": START.isoformat(), "days": DAYS}))
    return out


@pytest.fixture()
def client(data_dir):
    return TestClient(create_app(data_dir, day=1))


def rows_in(data_dir, folder, until):
    """Ground truth straight from the files: everything that has arrived up to `until`."""
    rows = []
    for p in (data_dir / folder).glob("day=*/part-0.jsonl"):
        if date.fromisoformat(p.parent.name[4:]) <= until:
            rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    return rows


def fetch_all(client, path, **params):
    rows, page = [], 1
    while page is not None:
        body = client.get(path, params={**params, "page": page}).json()
        rows += body["data"]
        page = body["next_page"]
    return rows


def test_clock(client):
    assert client.get("/_clock").json() == {"day": 1, "date": "2026-10-01", "max_day": DAYS}
    assert client.post("/_clock", json={"advance": 2}).json()["date"] == "2026-10-03"
    assert client.post("/_clock", json={"day": 10}).json()["day"] == 10
    assert client.post("/_clock", json={"day": 0}).status_code == 422
    assert client.post("/_clock", json={"day": DAYS + 1}).status_code == 422
    assert client.post("/_clock", json={"day": 2, "advance": 1}).status_code == 422


def test_clock_hides_future_days(client, data_dir):
    client.post("/_clock", json={"day": 5})
    got = fetch_all(client, "/gl-entries")
    assert len(got) == len(rows_in(data_dir, "gl_entries", START + timedelta(days=4)))
    client.post("/_clock", json={"day": DAYS})
    assert len(fetch_all(client, "/gl-entries")) == len(rows_in(data_dir, "gl_entries", END))


def test_paging_returns_every_row_once(client):
    client.post("/_clock", json={"day": DAYS})
    small = fetch_all(client, "/invoices", page_size=7)
    big = client.get("/invoices", params={"page_size": 1000}).json()
    assert small == big["data"]
    assert big["total"] == len(small) and big["next_page"] is None


def test_page_past_the_end_is_empty(client):
    body = client.get("/customers", params={"page": 999}).json()
    assert body["data"] == [] and body["next_page"] is None


def test_updated_since_is_inclusive(client):
    client.post("/_clock", json={"day": DAYS})
    every = fetch_all(client, "/invoices")
    cursor = every[len(every) // 2]["updated_at"]
    got = fetch_all(client, "/invoices", updated_since=cursor)
    assert got and all(r["updated_at"] >= cursor for r in got)
    assert any(r["updated_at"] == cursor for r in got)
    # Same instant written with an offset instead of Z gives the same result.
    assert fetch_all(client, "/invoices", updated_since=cursor.replace("Z", "+00:00")) == got


def test_late_rows_are_missed_by_a_naive_cursor(client, data_dir):
    """The cursor trap from section 6 of the plan, reproduced against the API."""
    client.post("/_clock", json={"day": 10})
    cursor = max(r["updated_at"] for r in fetch_all(client, "/gl-entries"))

    client.post("/_clock", json={"day": DAYS})
    incremental = {r["entry_id"] for r in fetch_all(client, "/gl-entries", updated_since=cursor)}
    everything = {r["entry_id"] for r in fetch_all(client, "/gl-entries")}
    seen_by_day_10 = {r["entry_id"] for r in rows_in(data_dir, "gl_entries", START + timedelta(days=9))}

    missed = everything - seen_by_day_10 - incremental
    assert missed, "expected late GL lines that arrive after day 10 with an updated_at before the cursor"


def test_accounts_is_latest_snapshot(client):
    first = {a["account_no"]: a for a in client.get("/accounts").json()["data"]}
    client.post("/_clock", json={"day": 10})
    second = {a["account_no"]: a for a in client.get("/accounts").json()["data"]}
    assert 6860 not in first and 6860 in second
    assert first[6940]["is_active"] and not second[6940]["is_active"]


def test_schema_change_visible_through_api(client):
    client.post("/_clock", json={"day": DAYS})
    rows = fetch_all(client, "/invoices")
    assert any("lines" in r for r in rows) and any("lines" not in r for r in rows)


def test_index_lists_endpoints(client):
    body = client.get("/").json()
    assert "/gl-entries" in body["endpoints"] and body["docs"].endswith("/docs")


def test_no_data_gives_503(tmp_path):
    c = TestClient(create_app(tmp_path / "missing", day=1))
    assert c.get("/invoices").status_code == 503
