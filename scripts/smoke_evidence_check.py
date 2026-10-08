#!/usr/bin/env python3
"""Read-only write-smoke evidence for one county (aggregates only, no names).

Run by Leads Ops wherever ``MONGODB_URI`` is set (the agent box has none):

    python scripts/smoke_evidence_check.py --county Lee --state FL --hours 24

It reads ``arrests`` rows for that county written in the window and prints one
JSON line: row count, booking-key shapes and uniqueness, how many rows have an
empty, zero or positive bond, charges, booking date/time, release fields,
mugshot, DOB and address. It never prints a name, DOB, address or booking
number, and it never writes. Paste the JSON into the ``result`` of the
matching ``docs/recon/smoke_evidence.json`` row (see
docs/recon/FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


def key_shape(value: Any) -> str:
    """``1033660`` -> ``NNNNNNN``; ``GCSO26JBN000123`` -> ``AAAANNAAANNNNNN``."""
    return re.sub(r"\d", "N", re.sub(r"[A-Za-z]", "A", str(value or "")))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _bond_bucket(doc: dict) -> str:
    raw = doc.get("bond_amount_raw")
    if raw is None:
        raw = doc.get("bond_amount")
    if _blank(raw):
        return "empty"
    try:
        amount = float(str(raw).replace("$", "").replace(",", "").strip())
    except ValueError:
        return "text"
    if amount == 0:
        return "zero"
    return "positive" if amount > 0 else "negative"


def summarize(docs: Iterable[dict]) -> dict:
    """Aggregate evidence for a list of stored arrest docs (pure; no PII out)."""
    rows = list(docs)
    keys = [str(d.get("booking_number") or "") for d in rows]
    nonblank = [k for k in keys if k]
    charges = [str(d.get("charges") or "") for d in rows]
    scraped = sorted(
        str(d.get("scraped_at") or d.get("updated_at") or "") for d in rows
        if d.get("scraped_at") or d.get("updated_at")
    )
    return {
        "rows": len(rows),
        "booking_number_blank": len(keys) - len(nonblank),
        "booking_number_unique": len(set(nonblank)),
        "booking_number_duplicates": len(nonblank) - len(set(nonblank)),
        "booking_number_shapes": dict(Counter(key_shape(k) for k in nonblank).most_common(5)),
        "bond": dict(Counter(_bond_bucket(d) for d in rows)),
        "bond_type_present": sum(1 for d in rows if not _blank(d.get("bond_type"))),
        "with_charges": sum(1 for c in charges if c.strip()),
        "multi_charge": sum(1 for c in charges if " | " in c),
        "with_booking_date": sum(1 for d in rows if not _blank(d.get("booking_date"))),
        "with_booking_time": sum(1 for d in rows if not _blank(d.get("booking_time"))),
        "status": dict(Counter(str(d.get("status") or "") for d in rows).most_common(6)),
        "with_release_date": sum(1 for d in rows if not _blank(d.get("release_date"))),
        "with_mugshot": sum(1 for d in rows if not _blank(d.get("mugshot_url"))),
        "with_dob": sum(1 for d in rows if not _blank(d.get("dob"))),
        "with_address": sum(1 for d in rows if not _blank(d.get("address"))),
        "first_scraped_at": scraped[0] if scraped else "",
        "last_scraped_at": scraped[-1] if scraped else "",
    }


def county_query(county: str, state: str, since: datetime) -> dict:
    """Same county/state rule as the lead list; Florida owns rows with no state."""
    name = re.escape(county.strip())
    st = state.strip().upper()
    if st == "FL":
        state_cond: dict = {"$or": [
            {"state": {"$in": ["FL", "fl", "Florida", "FLORIDA"]}},
            {"state": None}, {"state": ""}, {"state": {"$exists": False}},
        ]}
    else:
        state_cond = {"state": {"$in": [st, st.lower(), st.title()]}}
    return {"$and": [
        {"county": {"$regex": f"^{name}(?:\\s+County)?$", "$options": "i"}},
        state_cond,
        {"$or": [
            {"scraped_at": {"$gte": since}},
            {"scraped_at": {"$gte": since.isoformat()}},
            {"updated_at": {"$gte": since}},
        ]},
    ]}


PROJECTION = {
    "_id": 0, "booking_number": 1, "bond_amount": 1, "bond_amount_raw": 1, "bond_type": 1,
    "charges": 1, "booking_date": 1, "booking_time": 1, "status": 1, "release_date": 1,
    "mugshot_url": 1, "dob": 1, "address": 1, "scraped_at": 1, "updated_at": 1,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--county", required=True)
    parser.add_argument("--state", default="FL")
    parser.add_argument("--hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    uri = os.getenv("MONGODB_URI", "")
    if not uri:
        print(json.dumps({"result": "error", "error": "MONGODB_URI is not set (run where Leads Ops has it)"}))
        return 1
    from pymongo import MongoClient

    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")  # same default as config/settings.py
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    db = client.get_database(db_name)
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    docs = db["arrests"].find(county_query(args.county, args.state, since), PROJECTION)
    out = {
        "label": f"{args.county} ({args.state.upper()})",
        "window_hours": args.hours,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **summarize(docs),
    }
    out["result"] = "ok" if out["rows"] else "no_rows"
    print(json.dumps(out, sort_keys=True))
    return 0 if out["rows"] else 2


if __name__ == "__main__":
    sys.exit(main())
