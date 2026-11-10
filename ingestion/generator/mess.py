"""Realistic mess, applied on top of clean data from generate.py.

Each step is a separate function so it can be switched off (`--mess none`, or a comma list),
and each one returns counts of what it did. Those counts go into data/_manifest.json, so you
know exactly how many duplicates, late rows and nulls bronze *should* end up with.

Order matters and is fixed: schema_change -> nulls -> late -> duplicates. Duplicates run last,
so a duplicated row is an exact copy of whatever the source "sent".

Updates (invoice status changes, customer changes) are not mess: they're real source
behaviour and are produced by generate.py.
"""

from __future__ import annotations

import copy
import random
from collections import Counter
from datetime import date, timedelta

from generator.model import Dataset

SCHEMA_CHANGE_DAY = 15  # 1-based day number on which the source ships invoice "v2"
V2_FIELDS = ("currency", "kid", "lines")


def norwegian_amount(value: float) -> str:
    """1234.5 -> '1 234,50' (space thousands separator, decimal comma)."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


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
