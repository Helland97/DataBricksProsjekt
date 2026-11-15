"""Realistic mess, applied on top of clean data from generate.py.

Each step is a separate function so it can be switched off (`--mess none`, or a comma list),
and each one returns counts of what it did. Those counts go into data/_manifest.json, so you
know exactly how many duplicates, late rows and nulls bronze *should* end up with.

Order matters and is fixed:
schema_change -> nulls -> invoice_gap -> double_billing -> backdated -> late -> duplicates.
Duplicates run last, so a duplicated row is an exact copy of whatever the source "sent".

invoice_gap, double_billing and backdated are "audit mess": planted accounting errors that the
dbt controls in step 3 must find (STEG3-PLAN.md, section 4 and 9). The manifest is their answer key.

Updates (invoice status changes, customer changes) are not mess: they're real source
behaviour and are produced by generate.py.
"""

from __future__ import annotations

import copy
import random
from collections import Counter
from datetime import date, datetime, time, timedelta

from generator.model import Dataset, iso, kid_number, kroner

SCHEMA_CHANGE_DAY = 15  # 1-based day number on which the source ships invoice "v2"
V2_FIELDS = ("currency", "kid", "lines")

# Audit mess. Day numbers are 1-based, like SCHEMA_CHANGE_DAY.
INVOICE_GAP_DAYS = (10, 20)      # the removed invoice is dated on one of these days
DOUBLE_BILLING_DAYS = (20, 27)   # the original is dated on one of these days; the copy on the last day
BACKDATED_DAY = 8                # the day the backdated vouchers are posted (after the period close)
BACKDATED_EXPENSES = [           # account, description, VAT rate
    (6340, "Strøm september, etterregistrert", 25),
    (6800, "Kontorrekvisita september, etterregistrert", 25),
]


def norwegian_amount(value: float) -> str:
    """1234.5 -> '1 234,50' (space thousands separator, decimal comma)."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _next_voucher_no(data: Dataset) -> int:
    return max(e["voucher_no"] for e in data["gl_entries"]) + 1


def _resort(data: Dataset, *entities: str) -> None:
    """Keep rows sorted by arrival day and updated_at, like generate.py leaves them."""
    for entity in entities:
        data[entity].sort(key=lambda r: (r["_day"], r.get("updated_at", "")))


def schema_change(data: Dataset, rng: random.Random, start: date, **_) -> dict:
    """Invoices before SCHEMA_CHANGE_DAY are 'v1': no currency, kid or nested lines.

    From that day on rows are 'v2', and ~5 % of them send amount_incl_vat as a
    Norwegian-formatted string instead of a number (a type change). The first v2 row on the change day
    always does, so all three kinds of schema change (new columns, nested list, type change) land on
    the same day.
    """
    change_day = start + timedelta(days=SCHEMA_CHANGE_DAY - 1)
    v1 = stringified = 0
    for row in data["invoices"]:  # sorted by arrival day and updated_at
        if row["_day"] < change_day:
            for f in V2_FIELDS:
                row.pop(f, None)
            v1 += 1
        elif (row["_day"] == change_day and stringified == 0) or rng.random() < 0.05:
            row["amount_incl_vat"] = norwegian_amount(row["amount_incl_vat"])
            stringified += 1
    return {
        "change_day": change_day.isoformat(),
        "invoices_v1": v1,
        "invoices_v2": len(data["invoices"]) - v1,
        "amount_as_string": stringified,
    }


# entity -> (field, probability, value to put in)
NULL_RULES = [
    ("customers", "email", 0.05, None),
    ("customers", "org_no", 0.03, ""),
    ("invoices", "due_date", 0.02, None),
    ("invoices", "vat_amount", 0.02, None),
]


def nulls(data: Dataset, rng: random.Random, **_) -> dict:
    """Blank out some fields. Empty org_no is an empty string, not null, on purpose."""
    counts: Counter[str] = Counter()
    for entity, field, p, value in NULL_RULES:
        for row in data[entity]:
            if rng.random() < p:
                row[field] = value
                counts[f"{entity}.{field}"] += 1
    return dict(counts)


def invoice_gap(data: Dataset, rng: random.Random, start: date, **_) -> dict:
    """Remove every version of one invoice, and its GL vouchers (invoice, payment, credit note).

    Like an invoice that was deleted in the ERP: the number is missing in both the invoice series
    and the voucher series. The GL still balances and AR still reconciles, so only the gap
    controls can see it.
    """
    lo, hi = (start + timedelta(days=d - 1) for d in INVOICE_GAP_DAYS)
    candidates = sorted({
        r["invoice_id"] for r in data["invoices"]
        if lo <= date.fromisoformat(r["invoice_date"]) <= hi
    })
    if not candidates:
        return {"skipped": "no invoices in the window"}
    victim = rng.choice(candidates)
    vouchers = sorted({e["voucher_no"] for e in data["gl_entries"] if e["invoice_id"] == victim})
    data["invoices"] = [r for r in data["invoices"] if r["invoice_id"] != victim]
    data["gl_entries"] = [e for e in data["gl_entries"] if e["invoice_id"] != victim]
    return {"invoice_id": victim, "vouchers_removed": vouchers}


def double_billing(data: Dataset, rng: random.Random, start: date, end: date, **_) -> dict:
    """Re-issue one invoice on the last day with the next free number: same customer, lines and amount.

    Issued after every generated row, so the new number is also the latest one and the number
    series stays in date order. Technically it's a new, valid invoice with its own number, KID and
    voucher; only a business rule (same customer + amount + lines within 14 days) can see it.
    """
    lo, hi = (start + timedelta(days=d - 1) for d in DOUBLE_BILLING_DAYS)
    sent = [
        r for r in data["invoices"]
        if r["status"] == "sent" and lo <= date.fromisoformat(r["invoice_date"]) <= hi
    ]
    if not sent:
        return {"skipped": "no invoices in the window"}
    original = rng.choice(sent)
    new_id = max(r["invoice_id"] for r in data["invoices"]) + 1
    issued = datetime.combine(end, time(18, 30))  # after office hours: later than every generated row

    dup = copy.deepcopy(original)
    dup.update(
        invoice_id=new_id,
        invoice_date=end.isoformat(),
        updated_at=iso(issued),
        _day=end,
        kid=kid_number(f"{original['customer_id']}{new_id}"),
    )
    if original["due_date"]:
        terms = date.fromisoformat(original["due_date"]) - date.fromisoformat(original["invoice_date"])
        dup["due_date"] = (end + terms).isoformat()
    data["invoices"].append(dup)

    voucher_no = _next_voucher_no(data)
    new_lines = [
        dict(
            e,
            voucher_no=voucher_no,
            entry_id=f"{voucher_no}-{e['line_no']}",
            entry_date=end.isoformat(),
            description=f"Faktura {new_id}",
            invoice_id=new_id,
            updated_at=iso(issued),
            _day=end,
        )
        for e in data["gl_entries"]
        if e["invoice_id"] == original["invoice_id"] and e["description"].startswith("Faktura")
    ]
    data["gl_entries"].extend(new_lines)
    _resort(data, "invoices", "gl_entries")
    return {
        "original_invoice_id": original["invoice_id"],
        "duplicate_invoice_id": new_id,
        "voucher_no": voucher_no,
    }


def backdated(data: Dataset, rng: random.Random, start: date, **_) -> dict:
    """Post two expense vouchers on BACKDATED_DAY with entry_date in the previous month.

    The previous month is closed (dbt seed accounting_periods: closed 5 days into this month), so
    these are postings into a closed period. Each voucher balances, so only the period control
    can see them.
    """
    posted_day = start + timedelta(days=BACKDATED_DAY - 1)
    entry_date = start - timedelta(days=1)  # last day of the previous month
    vouchers = []
    for account, description, vat_rate in BACKDATED_EXPENSES:
        voucher_no = _next_voucher_no(data)
        posted = datetime.combine(posted_day, time(9)) + timedelta(seconds=rng.randint(0, 8 * 3600))
        net = rng.randint(1500, 6000) * 100  # øre
        vat = round(net * vat_rate / 100)
        lines = [(account, net, 0), (2710, vat, 0), (2400, 0, net + vat)]
        for n, (account_no, debit, credit) in enumerate(lines, start=1):
            data["gl_entries"].append({
                "entry_id": f"{voucher_no}-{n}",
                "voucher_no": voucher_no,
                "line_no": n,
                "entry_date": entry_date.isoformat(),
                "account_no": account_no,
                "description": description,
                "debit": kroner(debit),
                "credit": kroner(credit),
                "invoice_id": None,
                "customer_id": None,
                "updated_at": iso(posted),
                "_day": posted_day,
            })
        vouchers.append(voucher_no)
    _resort(data, "gl_entries")
    return {"entry_date": entry_date.isoformat(), "posted_day": posted_day.isoformat(), "vouchers": vouchers}


def late(data: Dataset, rng: random.Random, end: date, rate: float = 0.03, max_days: int = 5, **_) -> dict:
    """Some GL lines arrive 1-5 days late, *keeping their original updated_at*.

    This is the cursor trap: when they show up, the pipeline's cursor is already past
    their updated_at, so a naive `updated_at > last_value` never sees them. It also leaves
    vouchers temporarily unbalanced in bronze until the missing line arrives.
    """
    delays: Counter[int] = Counter()
    for row in data["gl_entries"]:
        if rng.random() >= rate:
            continue
        new_day = min(row["_day"] + timedelta(days=rng.randint(1, max_days)), end)
        if new_day > row["_day"]:
            delays[(new_day - row["_day"]).days] += 1
            row["_day"] = new_day
    return {"gl_entries": sum(delays.values()), "by_delay_days": dict(sorted(delays.items()))}


def duplicates(data: Dataset, rng: random.Random, end: date, rate: float = 0.02, **_) -> dict:
    """Send ~2 % of rows twice: half in the same day (same page), half again the next day."""
    counts = {}
    for entity in ("customers", "invoices", "gl_entries"):
        extra = []
        same_day = next_day = 0
        for row in data[entity]:
            if rng.random() >= rate:
                continue
            dup = copy.deepcopy(row)
            if rng.random() < 0.5 or row["_day"] >= end:
                same_day += 1
            else:
                dup["_day"] = row["_day"] + timedelta(days=1)
                next_day += 1
            extra.append(dup)
        data[entity].extend(extra)
        data[entity].sort(key=lambda r: (r["_day"], r.get("updated_at", "")))
        counts[entity] = {"same_day": same_day, "next_day": next_day}
    return counts


MESS_STEPS = {
    "schema_change": schema_change,
    "nulls": nulls,
    "invoice_gap": invoice_gap,
    "double_billing": double_billing,
    "backdated": backdated,
    "late": late,
    "duplicates": duplicates,
}


def apply(data: Dataset, steps: list[str], seed: int, start: date, end: date) -> dict:
    """Run the selected steps in the fixed order. Each step gets its own seeded RNG,
    so switching one step off doesn't change what the others do."""
    report = {}
    for name in MESS_STEPS:
        if name in steps:
            rng = random.Random(f"{seed}-{name}")
            report[name] = MESS_STEPS[name](data, rng, start=start, end=end)
        else:
            report[name] = "off"
    return report
