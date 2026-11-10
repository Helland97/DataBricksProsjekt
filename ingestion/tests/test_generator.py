import json
from collections import Counter, defaultdict
from datetime import date, timedelta

import pytest

from generator import mess
from generator.generate import generate, kid_number, org_no, write

START = date(2026, 10, 1)
DAYS = 30
END = START + timedelta(days=DAYS - 1)


def ore(value) -> int:
    return round(value * 100)


@pytest.fixture(scope="module")
def clean():
    return generate(START, DAYS, seed=42)


@pytest.fixture()
def messy():
    data = generate(START, DAYS, seed=42)
    report = mess.apply(data, list(mess.MESS_STEPS), seed=42, start=START, end=END)
    return data, report


# --- Clean data ---------------------------------------------------------------


def test_deterministic():
    assert generate(START, 5, seed=7) == generate(START, 5, seed=7)
    assert generate(START, 5, seed=7) != generate(START, 5, seed=8)


def test_every_voucher_balances(clean):
    debit, credit = defaultdict(int), defaultdict(int)
    for e in clean["gl_entries"]:
        debit[e["voucher_no"]] += ore(e["debit"])
        credit[e["voucher_no"]] += ore(e["credit"])
    assert debit == credit
    assert len(debit) > 100


def test_each_gl_line_is_one_sided(clean):
    for e in clean["gl_entries"]:
        assert (e["debit"] == 0) != (e["credit"] == 0), e


def test_keys_are_unique(clean):
    assert len({e["entry_id"] for e in clean["gl_entries"]}) == len(clean["gl_entries"])
    # Customers and invoices have several versions; (key, updated_at) is unique.
    for entity, key in [("customers", "customer_id"), ("invoices", "invoice_id")]:
        versions = [(r[key], r["updated_at"]) for r in clean[entity]]
        assert len(set(versions)) == len(versions)


def test_gl_accounts_exist_in_chart(clean):
    accounts = {a["account_no"] for a in clean["accounts"]}
    assert {e["account_no"] for e in clean["gl_entries"]} <= accounts


def test_invoices_reference_known_customers(clean):
    customers = {c["customer_id"] for c in clean["customers"]}
    assert {i["customer_id"] for i in clean["invoices"]} <= customers


def test_invoice_totals_match_lines(clean):
    for inv in clean["invoices"]:
        net = sum(ore(line["amount_ex_vat"]) for line in inv["lines"])
        assert net == ore(inv["amount_ex_vat"])
        assert ore(inv["amount_ex_vat"]) + ore(inv["vat_amount"]) == ore(inv["amount_incl_vat"])


def test_receivables_reconcile(clean):
    """Balance on 1500 = open (sent/overdue) invoices, using each invoice's latest version."""
    latest = {}
    for inv in sorted(clean["invoices"], key=lambda r: r["updated_at"]):
        latest[inv["invoice_id"]] = inv
    open_ar = sum(ore(i["amount_incl_vat"]) for i in latest.values() if i["status"] in ("sent", "overdue"))
    ledger = sum(ore(e["debit"]) - ore(e["credit"]) for e in clean["gl_entries"] if e["account_no"] == 1500)
    assert ledger == open_ar


def test_invoice_status_updates_exist(clean):
    statuses = Counter(i["status"] for i in clean["invoices"])
    assert statuses["sent"] and statuses["paid"] and statuses["overdue"] and statuses["credited"]


def test_chart_of_accounts_changes_on_day_10(clean):
    by_day = defaultdict(dict)
    for a in clean["accounts"]:
        by_day[a["_day"]][a["account_no"]] = a
    first, second = sorted(by_day)
    assert 6860 not in by_day[first] and 6860 in by_day[second]
    assert by_day[first][6940]["is_active"] and not by_day[second][6940]["is_active"]


def test_check_digits():
    import random

    rng = random.Random(1)
    for _ in range(50):
        n = org_no(rng)
        weights = [3, 2, 7, 6, 5, 4, 3, 2, 1]
        assert len(n) == 9 and sum(int(d) * w for d, w in zip(n, weights)) % 11 == 0
    assert kid_number("7992739871") == "79927398713"  # textbook Luhn example


# --- Mess ---------------------------------------------------------------------


def test_duplicates_are_exact_copies(messy):
    data, report = messy
    rows = [json.dumps({k: v for k, v in r.items() if k != "_day"}, sort_keys=True, default=str)
            for r in data["gl_entries"]]
    dupes = sum(n - 1 for n in Counter(rows).values() if n > 1)
    expected = report["duplicates"]["gl_entries"]
    assert dupes == expected["same_day"] + expected["next_day"] > 0


def test_late_rows_keep_old_updated_at(messy):
    data, report = messy
    late = [e for e in data["gl_entries"] if e["_day"] > date.fromisoformat(e["updated_at"][:10])]
    # Next-day duplicates also look "late", so there are at least as many as the late step reports.
    assert len(late) >= report["late"]["gl_entries"] > 0


def test_schema_change(messy):
    data, report = messy
    change = date.fromisoformat(report["schema_change"]["change_day"])
    before = [i for i in data["invoices"] if i["_day"] < change]
    after = [i for i in data["invoices"] if i["_day"] >= change]
    assert before and all("lines" not in i and "kid" not in i for i in before)
    assert after and all("lines" in i and "currency" in i for i in after)
    assert any(isinstance(i["amount_incl_vat"], str) for i in after)
    assert not any(isinstance(i["amount_incl_vat"], str) for i in before)
    # The type change happens on the change day itself, together with the new columns.
    assert any(isinstance(i["amount_incl_vat"], str) for i in after if i["_day"] == change)


def test_nulls(messy):
    data, report = messy
    assert sum(c["email"] is None for c in data["customers"]) >= report["nulls"]["customers.email"] > 0
    assert any(c["org_no"] == "" for c in data["customers"])
    assert any(i["due_date"] is None for i in data["invoices"])


def test_steps_are_independent():
    """Switching one step off must not change what the others do."""
    a = generate(START, DAYS, seed=42)
    b = generate(START, DAYS, seed=42)
    ra = mess.apply(a, ["nulls", "late"], seed=42, start=START, end=END)
    rb = mess.apply(b, ["late"], seed=42, start=START, end=END)
    assert ra["late"] == rb["late"]


def test_write_layout(tmp_path, messy):
    data, _ = messy
    out = tmp_path / "data"
    counts = write(data, out)
    files = list(out.glob("gl_entries/day=*/part-0.jsonl"))
    assert len(files) == DAYS
    lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in out.glob("gl_entries/*/*.jsonl"))
    assert lines == counts["gl_entries"]
    first = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert "_day" not in first
