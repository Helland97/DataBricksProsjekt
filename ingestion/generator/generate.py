"""Generate synthetic Norwegian accounting data, one simulated day at a time.

    uv run python -m generator.generate --days 30 --seed 42
    uv run python -m generator.generate --mess none          # clean data, for comparison
    uv run python -m generator.generate --mess duplicates,late

Output (gitignored), one folder per entity and arrival day:

    data/<entity>/day=YYYY-MM-DD/part-0.jsonl
    data/_manifest.json     # parameters + what the mess step did

`generate()` returns *clean* data: unique versions, balanced vouchers, one schema.
The realistic mess is applied afterwards by mess.py, so each kind can be switched off.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path

from faker import Faker

from generator import mess
from generator.model import (
    ENTITIES,
    Account,
    Customer,
    Dataset,
    GlLine,
    Invoice,
    InvoiceLine,
    Voucher,
)

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data"

# --- Chart of accounts (subset of NS 4102) ------------------------------------

CHART_OF_ACCOUNTS = [
    Account(1200, "Maskiner og anlegg", "asset"),
    Account(1250, "Inventar", "asset"),
    Account(1460, "Innkjøpte varer for videresalg", "asset"),
    Account(1500, "Kundefordringer", "asset"),
    Account(1900, "Kontanter", "asset"),
    Account(1920, "Bankinnskudd", "asset"),
    Account(2000, "Aksjekapital", "equity"),
    Account(2050, "Annen egenkapital", "equity"),
    Account(2400, "Leverandørgjeld", "liability"),
    Account(2600, "Forskuddstrekk", "liability"),
    Account(2700, "Utgående merverdiavgift", "liability", vat_code="3"),
    Account(2710, "Inngående merverdiavgift", "liability", vat_code="1"),
    Account(2740, "Oppgjørskonto merverdiavgift", "liability"),
    Account(2770, "Skyldig arbeidsgiveravgift", "liability"),
    Account(2940, "Skyldige feriepenger", "liability"),
    Account(3000, "Salgsinntekt, avgiftspliktig", "revenue", vat_code="3"),
    Account(3100, "Salgsinntekt, avgiftsfri", "revenue", vat_code="5"),
    Account(3400, "Offentlig tilskudd", "revenue"),
    Account(3600, "Leieinntekt", "revenue"),
    Account(4000, "Innkjøp av råvarer og halvfabrikata", "expense", vat_code="1"),
    Account(4300, "Innkjøp av varer for videresalg", "expense", vat_code="1"),
    Account(5000, "Lønn til ansatte", "expense"),
    Account(5400, "Arbeidsgiveravgift", "expense"),
    Account(6300, "Leie lokale", "expense", vat_code="1"),
    Account(6340, "Lys, varme", "expense", vat_code="1"),
    Account(6540, "Inventar", "expense", vat_code="1"),
    Account(6700, "Revisjons- og regnskapshonorar", "expense", vat_code="1"),
    Account(6800, "Kontorrekvisita", "expense", vat_code="1"),
    Account(6900, "Telefon", "expense", vat_code="1"),
    Account(6940, "Porto", "expense", vat_code="1"),
    Account(7100, "Bilgodtgjørelse, opplysningspliktig", "expense"),
    Account(7140, "Reisekostnad, ikke opplysningspliktig", "expense"),
    Account(7320, "Reklamekostnad", "expense", vat_code="1"),
    Account(7500, "Forsikringspremie", "expense"),
    Account(7770, "Bank- og kortgebyrer", "expense"),
    Account(8050, "Annen renteinntekt", "finance"),
    Account(8150, "Annen rentekostnad", "finance"),
]

# Chart of accounts change: on this day one account is added and one deactivated.
# The accounts endpoint has no updated_at, so this is only picked up by a full load.
COA_CHANGE_DAY = 10
COA_NEW_ACCOUNT = Account(6860, "Møte, kurs, oppdatering", "expense", vat_code="1")
COA_DEACTIVATED = 6940

PRODUCTS = [  # description, unit price range in NOK, VAT rate
    ("Konsulenttime", (950, 1650), 25),
    ("Prosjektledelse, time", (1100, 1800), 25),
    ("Lisens, månedlig", (300, 2500), 25),
    ("Support-avtale", (1500, 6000), 25),
    ("Opplæring, dagskurs", (4500, 12000), 0),
    ("Maskinvare", (2000, 25000), 25),
    ("Reiseutlegg, viderefakturert", (300, 4000), 0),
]

# account, description, amount range in NOK (ex. VAT), VAT rate, paid by bank directly
EXPENSES = [
    (4300, "Varekjøp", (2000, 40000), 25, False),
    (6340, "Strøm", (1500, 6000), 25, False),
    (6800, "Kontorrekvisita", (200, 3000), 25, False),
    (6900, "Mobilabonnement", (400, 2500), 25, False),
    (6940, "Porto og frakt", (100, 1500), 25, False),
    (7140, "Reise, togbillett", (300, 3500), 0, False),
    (7320, "Annonse i sosiale medier", (1000, 15000), 25, False),
    (7770, "Bankgebyr", (25, 400), 0, True),
]
MONTHLY_RENT_NOK = 38_000


# --- Helpers -------------------------------------------------------------------


def org_no(rng: random.Random) -> str:
    """Norwegian organisation number: 9 digits, mod 11 check digit."""
    weights = [3, 2, 7, 6, 5, 4, 3, 2]
    while True:
        digits = [rng.randint(8, 9)] + [rng.randint(0, 9) for _ in range(7)]
        rest = sum(d * w for d, w in zip(digits, weights)) % 11
        check = 0 if rest == 0 else 11 - rest
        if check != 10:
            return "".join(map(str, digits + [check]))


def kid_number(base: str) -> str:
    """KID with a mod 10 (Luhn) check digit, as used on Norwegian invoices."""
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return base + str((10 - total % 10) % 10)


def business_time(rng: random.Random, day: date) -> datetime:
    """A random timestamp during office hours on `day` (UTC)."""
    seconds = rng.randint(7 * 3600, 18 * 3600)
    return datetime.combine(day, time()) + timedelta(seconds=seconds)


# --- Generation ----------------------------------------------------------------


class Generator:
    def __init__(self, start: date, days: int, seed: int) -> None:
        self.start = start
        self.days = days
        self.end = start + timedelta(days=days - 1)
        self.rng = random.Random(seed)
        self.fake = Faker("no_NO")
        self.fake.seed_instance(seed)

        self.customers: dict[int, Customer] = {}
        self.customer_rows: list[dict] = []
        self.invoice_rows: list[dict] = []
        self.vouchers: list[Voucher] = []
        self.next_customer_id = 10001
        self.next_invoice_id = 100001

    def run(self) -> Dataset:
        for offset in range(self.days):
            day = self.start + timedelta(days=offset)
            self._customers_for_day(day, initial=40 if offset == 0 else 0)
            self._invoices_for_day(day)
            self._expenses_for_day(day)

        # Number vouchers in posting order, like a real ledger.
        self.vouchers.sort(key=lambda v: v.posted_at)
        for n, voucher in enumerate(self.vouchers, start=1):
            voucher.voucher_no = n

        data: Dataset = {
            "accounts": self._accounts(),
            "customers": self.customer_rows,
            "invoices": self.invoice_rows,
            "gl_entries": [r for v in self.vouchers for r in v.to_records()],
        }
        for rows in data.values():
            rows.sort(key=lambda r: (r["_day"], r.get("updated_at", "")))
        return data

    # accounts: full snapshot on day 1, and again when the chart of accounts changes
    def _accounts(self) -> list[dict]:
        rows = [a.to_record(self.start) for a in CHART_OF_ACCOUNTS]
        if self.days >= COA_CHANGE_DAY:
            change_day = self.start + timedelta(days=COA_CHANGE_DAY - 1)
            changed = [
                Account(a.account_no, a.name, a.account_type, a.vat_code, a.account_no != COA_DEACTIVATED)
                for a in CHART_OF_ACCOUNTS
            ] + [COA_NEW_ACCOUNT]
            changed.sort(key=lambda a: a.account_no)
            rows += [a.to_record(change_day) for a in changed]
        return rows

    def _customers_for_day(self, day: date, initial: int) -> None:
        new = initial or (1 if self.rng.random() < 0.6 else 0) + (1 if self.rng.random() < 0.15 else 0)
        for _ in range(new):
            ts = business_time(self.rng, day)
            c = Customer(
                customer_id=self.next_customer_id,
                name=self.fake.company(),
                org_no=org_no(self.rng),
                address=self.fake.street_address(),
                postal_code=self.fake.postcode(),
                city=self.fake.city(),
                email=self.fake.company_email(),
                payment_terms_days=self.rng.choice([10, 14, 14, 30, 30, 30]),
                created_at=ts,
                updated_at=ts,
            )
            self.next_customer_id += 1
            self.customers[c.customer_id] = c
            self.customer_rows.append(c.to_record())

        # Updates to existing customers (not the ones created today).
        for c in list(self.customers.values()):
            if c.created_at.date() == day or self.rng.random() > 0.03:
                continue
            change = self.rng.choice(["address", "terms", "email"])
            if change == "address":
                c.address = self.fake.street_address()
                c.postal_code = self.fake.postcode()
                c.city = self.fake.city()
            elif change == "terms":
                c.payment_terms_days = self.rng.choice([10, 14, 30, 45])
            else:
                c.email = self.fake.company_email()
            c.updated_at = business_time(self.rng, day)
            self.customer_rows.append(c.to_record())

    def _invoices_for_day(self, day: date) -> None:
        if day.weekday() >= 5:  # fewer invoices in weekends
            count = self.rng.randint(0, 2)
        else:
            count = self.rng.randint(5, 15)
        for _ in range(count):
            customer = self.rng.choice(list(self.customers.values()))
            created = business_time(self.rng, day)
            lines = []
            for n in range(1, self.rng.randint(1, 4) + 1):
                desc, (lo, hi), vat = self.rng.choice(PRODUCTS)
                lines.append(InvoiceLine(n, desc, self.rng.randint(1, 10), self.rng.randint(lo, hi) * 100, vat))
            inv = Invoice(
                invoice_id=self.next_invoice_id,
                customer_id=customer.customer_id,
                invoice_date=day,
                due_date=day + timedelta(days=customer.payment_terms_days),
                kid=kid_number(f"{customer.customer_id}{self.next_invoice_id}"),
                lines=lines,
            )
            self.next_invoice_id += 1
            self._invoice_lifecycle(inv, created)

    def _invoice_lifecycle(self, inv: Invoice, created: datetime) -> None:
        """Emit one row per status change, plus the matching GL vouchers."""
        self.invoice_rows.append(inv.to_record("sent", created))
        self._post_invoice(inv, created, reverse=False)

        r = self.rng.random()
        if r < 0.05:  # credited
            credited = business_time(self.rng, inv.invoice_date + timedelta(days=self.rng.randint(2, 10)))
            if credited.date() <= self.end:
                self.invoice_rows.append(inv.to_record("credited", credited))
                self._post_invoice(inv, credited, reverse=True)
            return

        paid_day = None
        if r < 0.92:
            terms = (inv.due_date - inv.invoice_date).days
            paid_day = inv.invoice_date + timedelta(days=max(1, terms + self.rng.randint(-7, 12)))

        overdue_day = inv.due_date + timedelta(days=1)
        # Strictly after the overdue day, so "overdue" can never get a later timestamp than "paid".
        if (paid_day is None or paid_day > overdue_day) and overdue_day <= self.end:
            self.invoice_rows.append(inv.to_record("overdue", business_time(self.rng, overdue_day)))

        if paid_day is not None and paid_day <= self.end:
            paid = business_time(self.rng, paid_day)
            self.invoice_rows.append(inv.to_record("paid", paid))
            total = inv.net_ore + inv.vat_ore
            self.vouchers.append(
                Voucher(
                    posted_at=paid,
                    description=f"Innbetaling faktura {inv.invoice_id}",
                    lines=[GlLine(1920, debit_ore=total), GlLine(1500, credit_ore=total)],
                    invoice_id=inv.invoice_id,
                    customer_id=inv.customer_id,
                )
            )

    def _post_invoice(self, inv: Invoice, ts: datetime, reverse: bool) -> None:
        """Outgoing invoice: debit 1500, credit 3000/3100 and 2700. A credit note reverses it."""
        net_25 = sum(line.net_ore for line in inv.lines if line.vat_rate == 25)
        net_0 = sum(line.net_ore for line in inv.lines if line.vat_rate == 0)
        vat = inv.vat_ore
        lines = [GlLine(1500, debit_ore=net_25 + net_0 + vat)]
        if net_25:
            lines.append(GlLine(3000, credit_ore=net_25))
        if net_0:
            lines.append(GlLine(3100, credit_ore=net_0))
        if vat:
            lines.append(GlLine(2700, credit_ore=vat))
        if reverse:
            lines = [GlLine(l.account_no, debit_ore=l.credit_ore, credit_ore=l.debit_ore) for l in lines]
        kind = "Kreditnota" if reverse else "Faktura"
        self.vouchers.append(
            Voucher(ts, f"{kind} {inv.invoice_id}", lines, inv.invoice_id, inv.customer_id)
        )

    def _expenses_for_day(self, day: date) -> None:
        if day.day == 1:
            self._expense(day, 6300, "Husleie", MONTHLY_RENT_NOK * 100, 0, bank=False)
        if day.weekday() >= 5:
            return
        for _ in range(self.rng.randint(1, 3)):
            account, desc, (lo, hi), vat_rate, bank = self.rng.choice(EXPENSES)
            net = self.rng.randint(lo, hi) * 100
            self._expense(day, account, desc, net, vat_rate, bank)

    def _expense(self, day: date, account: int, desc: str, net: int, vat_rate: int, bank: bool) -> None:
        vat = round(net * vat_rate / 100)
        lines = [GlLine(account, debit_ore=net)]
        if vat:
            lines.append(GlLine(2710, debit_ore=vat))
        lines.append(GlLine(1920 if bank else 2400, credit_ore=net + vat))
        self.vouchers.append(Voucher(business_time(self.rng, day), desc, lines))


def generate(start: date, days: int, seed: int) -> Dataset:
    return Generator(start, days, seed).run()


# --- Output --------------------------------------------------------------------


def write(data: Dataset, out: Path) -> dict[str, int]:
    """Write one JSONL file per entity and arrival day. Returns row counts per entity."""
    if out.exists():
        if any(out.iterdir()) and not (out / "_manifest.json").exists():
            raise SystemExit(f"Refusing to overwrite {out}: it was not created by this generator.")
        shutil.rmtree(out)

    counts = {}
    for entity in ENTITIES:
        by_day: dict[date, list[dict]] = {}
        for row in data[entity]:
            by_day.setdefault(row["_day"], []).append(row)
        for day, rows in by_day.items():
            path = out / entity / f"day={day.isoformat()}" / "part-0.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    clean = {k: v for k, v in row.items() if not k.startswith("_")}
                    f.write(json.dumps(clean, ensure_ascii=False) + "\n")
        counts[entity] = len(data[entity])
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 1))
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--mess",
        default="all",
        help=f"'all', 'none' or a comma list of: {', '.join(mess.MESS_STEPS)}",
    )
    args = p.parse_args()

    if args.mess == "all":
        steps = list(mess.MESS_STEPS)
    elif args.mess == "none":
        steps = []
    else:
        steps = [s.strip() for s in args.mess.split(",")]
        unknown = set(steps) - set(mess.MESS_STEPS)
        if unknown:
            p.error(f"unknown mess step(s): {', '.join(sorted(unknown))}")

    data = generate(args.start, args.days, args.seed)
    end = args.start + timedelta(days=args.days - 1)
    report = mess.apply(data, steps, seed=args.seed, start=args.start, end=end)
    counts = write(data, args.out)

    manifest = {
        "start": args.start.isoformat(),
        "end": end.isoformat(),
        "days": args.days,
        "seed": args.seed,
        "mess": report,
        "rows": counts,
    }
    (args.out / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
