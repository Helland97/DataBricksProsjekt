"""Record shapes for the four entities.

Amounts are kept as integer øre (1/100 NOK) inside the generator so vouchers balance
exactly, and are converted to kroner only when a record is written.

Every record also carries `_day`: the date the row becomes visible in the source
("arrival day"). In clean data it equals the date of `updated_at`; the late-arrival
mess in mess.py moves it forward. Keys starting with `_` are never written to the output
as fields; `_day` decides which day partition the row ends up in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

Record = dict  # one output row
Dataset = dict[str, list[Record]]  # entity name -> rows

ENTITIES = ("accounts", "customers", "invoices", "gl_entries")


def kroner(ore: int) -> float:
    return round(ore / 100, 2)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Account:
    account_no: int
    name: str
    account_type: str  # asset | equity | liability | revenue | expense | finance
    vat_code: str | None = None
    is_active: bool = True

    @property
    def account_class(self) -> int:
        return self.account_no // 1000

    def to_record(self, day: date) -> Record:
        return {**asdict(self), "account_class": self.account_class, "_day": day}


@dataclass
class Customer:
    customer_id: int
    name: str
    org_no: str
    address: str
    postal_code: str
    city: str
    email: str | None
    payment_terms_days: int
    created_at: datetime
    updated_at: datetime

    def to_record(self) -> Record:
        r = asdict(self)
        r["created_at"] = iso(self.created_at)
        r["updated_at"] = iso(self.updated_at)
        r["_day"] = self.updated_at.date()
        return r


@dataclass
class InvoiceLine:
    line_no: int
    description: str
    quantity: int
    unit_price_ore: int
    vat_rate: int  # percent, 25 or 0

    @property
    def net_ore(self) -> int:
        return self.quantity * self.unit_price_ore

    @property
    def vat_ore(self) -> int:
        return round(self.net_ore * self.vat_rate / 100)

    def to_record(self) -> Record:
        return {
            "line_no": self.line_no,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": kroner(self.unit_price_ore),
            "vat_rate": self.vat_rate,
            "amount_ex_vat": kroner(self.net_ore),
        }


@dataclass
class Invoice:
    invoice_id: int
    customer_id: int
    invoice_date: date
    due_date: date
    kid: str
    lines: list[InvoiceLine]
    currency: str = "NOK"

    @property
    def net_ore(self) -> int:
        return sum(line.net_ore for line in self.lines)

    @property
    def vat_ore(self) -> int:
        return sum(line.vat_ore for line in self.lines)

    def to_record(self, status: str, updated_at: datetime) -> Record:
        """One version of the invoice. A new version is emitted on every status change.

        The record always holds the full ("v2") shape. mess.schema_change strips the v2
        fields (currency, kid, lines) from rows before the schema change day.
        """
        return {
            "invoice_id": self.invoice_id,
            "customer_id": self.customer_id,
            "invoice_date": self.invoice_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "amount_ex_vat": kroner(self.net_ore),
            "vat_amount": kroner(self.vat_ore),
            "amount_incl_vat": kroner(self.net_ore + self.vat_ore),
            "status": status,
            "updated_at": iso(updated_at),
            "currency": self.currency,
            "kid": self.kid,
            "lines": [line.to_record() for line in self.lines],
            "_day": updated_at.date(),
        }


@dataclass
class GlLine:
    account_no: int
    debit_ore: int = 0
    credit_ore: int = 0


@dataclass
class Voucher:
    """One balanced posting (bilag). Numbered after all vouchers are generated."""

    posted_at: datetime
    description: str
    lines: list[GlLine]
    invoice_id: int | None = None
    customer_id: int | None = None
    voucher_no: int = 0
    entry_date: date = field(init=False)

    def __post_init__(self) -> None:
        self.entry_date = self.posted_at.date()

    def to_records(self) -> list[Record]:
        return [
            {
                "entry_id": f"{self.voucher_no}-{n}",
                "voucher_no": self.voucher_no,
                "line_no": n,
                "entry_date": self.entry_date.isoformat(),
                "account_no": line.account_no,
                "description": self.description,
                "debit": kroner(line.debit_ore),
                "credit": kroner(line.credit_ore),
                "invoice_id": self.invoice_id,
                "customer_id": self.customer_id,
                "updated_at": iso(self.posted_at),
                "_day": self.posted_at.date(),
            }
            for n, line in enumerate(self.lines, start=1)
        ]
