"""Fake ERP REST API on top of the files from generator.generate.

    uv run uvicorn fake_api.app:app --reload            # from ingestion/
    SIMULATED_DAY=7 uv run uvicorn fake_api.app:app      # start the clock on day 7
    DATA_DIR=/some/other/data uv run uvicorn ...

Endpoints:
    GET  /accounts                                   latest chart of accounts, no paging, no updated_at
    GET  /customers  ?updated_since=&page=&page_size=
    GET  /invoices   ?updated_since=&page=&page_size=
    GET  /gl-entries ?updated_since=&page=&page_size=
    GET  /_clock                                     current simulated day
    POST /_clock     {"day": 7}  or  {"advance": 1}  move the clock

The simulated clock filters on the *arrival day* (the day=... partition a row was written to), not on
updated_at. That's how late rows work: a GL line can appear on day 8 with an updated_at from day 5.

`updated_since` is inclusive (updated_at >= updated_since), like many real APIs. That means the row(s)
exactly on the cursor come back again on the next run, so the client has to deduplicate. dlt does that
for you: incremental deduplicates rows on the cursor boundary, and merge deduplicates on the primary key.

Files are read on every request, so regenerating data while the server runs just works.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator


class Utf8JSONResponse(JSONResponse):
    # JSON is always UTF-8, but Windows PowerShell 5.1 guesses Latin-1 without an explicit charset
    # and turns "Opplæring" into "OpplÃ¦ring".
    media_type = "application/json; charset=utf-8"

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Query parameters shared by the paged endpoints.
UpdatedSince = Annotated[datetime | None, Query(description="Inclusive: updated_at >= this")]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=1000)]


class Source:
    """Read-only access to the generated files, as seen on a given simulated day."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def manifest(self) -> dict:
        path = self.data_dir / "_manifest.json"
        if not path.exists():
            raise HTTPException(503, f"No data in {self.data_dir}. Run: uv run python -m generator.generate")
        return json.loads(path.read_text(encoding="utf-8"))

    def start(self) -> date:
        return date.fromisoformat(self.manifest()["start"])

    def max_day(self) -> int:
        return self.manifest()["days"]

    def partitions(self, folder: str, until: date) -> list[tuple[date, Path]]:
        """All day=... partitions of an entity up to and including `until`, oldest first."""
        parts = []
        for p in (self.data_dir / folder).glob("day=*/part-0.jsonl"):
            day = date.fromisoformat(p.parent.name.removeprefix("day="))
            if day <= until:
                parts.append((day, p))
        return sorted(parts)

    @staticmethod
    def read(path: Path) -> list[dict]:
        with path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def to_cursor(ts: datetime) -> str:
    """Normalise to the same string format the data uses, so plain string comparison works."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClockChange(BaseModel):
    day: int | None = None
    advance: int | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "ClockChange":
        if (self.day is None) == (self.advance is None):
            raise ValueError("send exactly one of 'day' or 'advance'")
        return self


def create_app(data_dir: Path | None = None, day: int | None = None) -> FastAPI:
    app = FastAPI(title="Fake ERP API", description=__doc__, default_response_class=Utf8JSONResponse)
    source = Source(Path(data_dir or os.environ.get("DATA_DIR", DEFAULT_DATA_DIR)))
    app.state.day = day if day is not None else int(os.environ.get("SIMULATED_DAY", "1"))

    def today(request: Request) -> date:
        return source.start() + timedelta(days=request.app.state.day - 1)

    def clock(request: Request) -> dict:
        return {
            "day": request.app.state.day,
            "date": today(request).isoformat(),
            "max_day": source.max_day(),
        }

    @app.get("/_clock")
    def get_clock(request: Request) -> dict:
        return clock(request)

    @app.post("/_clock")
    def set_clock(change: ClockChange, request: Request) -> dict:
        new_day = change.day if change.day is not None else request.app.state.day + change.advance
        if not 1 <= new_day <= source.max_day():
            raise HTTPException(422, f"day must be between 1 and {source.max_day()}, got {new_day}")
        request.app.state.day = new_day
        return clock(request)

    @app.get("/accounts")
    def accounts(request: Request) -> dict:
        """The newest chart of accounts snapshot up to today. A full load is the only way to see changes."""
        parts = source.partitions("accounts", today(request))
        if not parts:
            return {"data": []}
        return {"data": source.read(parts[-1][1])}

    def paged(request: Request, folder: str, key: str, updated_since, page: int, page_size: int) -> dict:
        """Shared logic for the three paged endpoints below.

        folder: entity folder in data/. key: primary key, used as a tie-breaker so paging is stable.
        """
        rows = [r for _, p in source.partitions(folder, today(request)) for r in source.read(p)]
        if updated_since is not None:
            since = to_cursor(updated_since)
            rows = [r for r in rows if r["updated_at"] >= since]
        # Stable order, like ORDER BY updated_at, id. Python's sort is stable, so duplicates stay adjacent.
        rows.sort(key=lambda r: (r["updated_at"], str(r[key])))

        total_pages = max(1, -(-len(rows) // page_size))
        chunk = rows[(page - 1) * page_size : page * page_size]
        return {
            "data": chunk,
            "page": page,
            "page_size": page_size,
            "total": len(rows),
            "total_pages": total_pages,
            "next_page": page + 1 if page < total_pages else None,
        }

    @app.get("/customers")
    def customers(
        request: Request, updated_since: UpdatedSince = None, page: Page = 1, page_size: PageSize = 100
    ) -> dict:
        return paged(request, "customers", "customer_id", updated_since, page, page_size)

    @app.get("/invoices")
    def invoices(
        request: Request, updated_since: UpdatedSince = None, page: Page = 1, page_size: PageSize = 100
    ) -> dict:
        return paged(request, "invoices", "invoice_id", updated_since, page, page_size)

    @app.get("/gl-entries")
    def gl_entries(
        request: Request, updated_since: UpdatedSince = None, page: Page = 1, page_size: PageSize = 100
    ) -> dict:
        return paged(request, "gl_entries", "entry_id", updated_since, page, page_size)

    @app.get("/")
    def index(request: Request) -> dict:
        """Front page: what's available and where the clock is."""
        return {
            "name": "Fake ERP API",
            "docs": str(request.url_for("swagger_ui_html")),
            "clock": clock(request),
            "endpoints": ["/accounts", "/customers", "/invoices", "/gl-entries", "/_clock"],
        }

    return app


app = create_app()
