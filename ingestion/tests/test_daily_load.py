"""The two rules in jobs/daily_load.py: which simulated day to load, and what counts as a correct load."""

from datetime import date

import pytest
from jobs.daily_load import BronzeMismatch, check_bronze, resolve_day

ANCHOR = date(2026, 12, 1)


@pytest.mark.parametrize(
    ("run_date", "expected"),
    [
        (date(2026, 12, 1), 1),  # the anchor date is day 1
        (date(2026, 12, 7), 7),
        (date(2026, 11, 20), 1),  # before the anchor: day 1
        (date(2027, 3, 1), 30),  # after the last day: the clock stays on 30
    ],
)
def test_auto_day_follows_the_run_date(run_date, expected):
    assert resolve_day("auto", run_date, anchor=ANCHOR, max_day=30) == expected


def test_explicit_day_ignores_the_run_date():
    assert resolve_day("7", date(2030, 1, 1), anchor=ANCHOR, max_day=30) == 7


@pytest.mark.parametrize("bad", ["0", "31", "seven"])
def test_invalid_day_is_rejected(bad):
    with pytest.raises(ValueError):
        resolve_day(bad, ANCHOR, anchor=ANCHOR, max_day=30)


CLEAN = {"missing": 0, "duplicates": 0, "stale": 0}


def test_clean_bronze_passes():
    check_bronze({"invoices": CLEAN, "customers": CLEAN})


def test_any_difference_fails_and_names_the_table():
    with pytest.raises(BronzeMismatch, match="invoices: missing=1"):
        check_bronze({"invoices": {**CLEAN, "missing": 1}, "customers": CLEAN})
