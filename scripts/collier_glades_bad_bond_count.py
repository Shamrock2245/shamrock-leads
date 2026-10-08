#!/usr/bin/env python3
"""Count Collier / Glades rows whose stored bond was not published by the source.

READ-ONLY. Prints JSON counts only: no names, booking numbers, ids or other
PII. Nothing is written. Plan: docs/ops/COLLIER_GLADES_BOND_CLEANUP_PLAN.md
(NOT RUN, awaiting Brendan's OK).

Why these rows are suspect (fixed in #139):

* Collier's daily report publishes no bond amount at all, so every non-empty
  stored bond was parsed from charge text ("... $750-$5K") or is the old "0"
  default for unknown.
* Glades' old parser read the first "Bond...: <number>" in a 15-row window that
  ran into the next inmates. In practice the only such label is the card-level
  "Bond Amount:", which prints "NO BOND" or "$0.00" (never the charge total,
  live check 2026-10-08), so stored "0.00" came from this card or the next
  card, and "0" is the old default for no match.

What cannot be identified from stored data: Glades and Collier rows keep no
per-charge bonds and no card text, so a stored positive Glades bond cannot be
traced to its own card versus the next card. Those rows are counted
(``glades.positive_unattributable``) but not targeted.

Usage (Leads Ops, wherever MONGODB_URI is set):
    python scripts/collier_glades_bad_bond_count.py --before 2026-10-09T00:00:00
    python scripts/collier_glades_bad_bond_count.py --before ... --print-filter Collier

``--before`` is the deploy time of the #139 fix (UTC ISO). Rows last scraped
after it were written by the fixed parsers and are never counted as bad.
Exit: 0 counts printed, 1 MONGODB_URI not set, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

ZERO_RAW = ("0", "0.0", "0.00")
COUNTIES = ("Collier", "Glades")
PROJECTION = {
    "_id": 0, "bond_amount": 1, "bond_amount_raw": 1, "total_bond_amount": 1,
    "staff_edits": 1, "bond_override": 1, "last_checked_mode": 1, "scraped_at": 1,
}
STAFF_PROVENANCE = [
    {"staff_edits": {"$exists": True}},
    {"bond_override": True},
    {"last_checked_mode": "MANUAL_CHARGE_BONDS"},
]


def county_clause(county: str) -> Dict[str, Any]:
    return {"$and": [
        {"county": {"$regex": f"^{re.escape(county)}(?:\\s+County)?$", "$options": "i"}},
        {"$or": [
            {"state": {"$in": ["FL", "fl", "Florida", "FLORIDA"]}},
            {"state": None}, {"state": ""}, {"state": {"$exists": False}},
        ]},
    ]}


def _naive_utc(text: str) -> datetime:
    """UTC as a naive datetime (how pymongo stores and compares BSON dates)."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _before_clause(before: str, *, extended_json: bool) -> Dict[str, Any]:
    # scraped_at is stored as an ISO string by the writer; older rows may hold a date.
    as_date: Any = {"$date": before + ("" if before.endswith("Z") else "Z")} if extended_json else _naive_utc(before)
    return {"$or": [
        {"scraped_at": {"$lt": before}},
        {"scraped_at": {"$type": "date", "$lt": as_date}},
        {"scraped_at": {"$exists": False}},
    ]}


def affected_filter(county: str, before: str, *, extended_json: bool = False) -> Dict[str, Any]:
    """Rows the cleanup plan would blank. Staff-provenance rows are never included.

    Collier: any non-empty ``bond_amount_raw`` or non-zero ``bond_amount``.
    Glades: only zero bonds ("0", "0.0", "0.00", or numeric 0 with a non-empty raw).
    """
    if county == "Collier":
        bond = {"$or": [
            {"$and": [{"bond_amount_raw": {"$exists": True}}, {"bond_amount_raw": {"$nin": ["", None]}}]},
            {"$and": [{"bond_amount": {"$exists": True}}, {"bond_amount": {"$nin": [0, None]}}]},
        ]}
    elif county == "Glades":
        bond = {"bond_amount_raw": {"$in": list(ZERO_RAW)}}
    else:
        raise ValueError(county)
    return {"$and": [county_clause(county), bond, _before_clause(before, extended_json=extended_json),
                     {"$nor": STAFF_PROVENANCE}]}


def _staff(doc: Dict[str, Any]) -> bool:
    return bool(doc.get("staff_edits")) or doc.get("bond_override") is True \
        or doc.get("last_checked_mode") == "MANUAL_CHARGE_BONDS"


def _raw_bucket(doc: Dict[str, Any]) -> str:
    raw = doc.get("bond_amount_raw")
    raw = "" if raw is None else str(raw).strip()
    if raw == "":
        try:
            return "empty" if float(doc.get("bond_amount") or 0) == 0 else "empty_raw_positive_numeric"
        except (TypeError, ValueError):
            return "empty"
    if raw in ZERO_RAW:
        return "zero"
    try:
        return "positive" if float(raw.replace(",", "").replace("$", "")) > 0 else "zero"
    except ValueError:
        return "text"


def _scraped_before(doc: Dict[str, Any], before: str) -> bool:
    val = doc.get("scraped_at")
    if val is None:
        return True
    if isinstance(val, datetime):
        v = val.astimezone(timezone.utc).replace(tzinfo=None) if val.tzinfo else val
        return v < _naive_utc(before)
    return str(val) < before


def summarize(county: str, docs: Iterable[Dict[str, Any]], before: str) -> Dict[str, Any]:
    """Counts only. ``docs`` carry just the PROJECTION fields."""
    total = staff = after = 0
    buckets: Counter = Counter()
    for doc in docs:
        total += 1
        if _staff(doc):
            staff += 1
            continue
        if not _scraped_before(doc, before):
            after += 1
            continue
        buckets[_raw_bucket(doc)] += 1
    out: Dict[str, Any] = {
        "rows": total,
        "skipped_staff_provenance": staff,
        "last_scraped_after_fix": after,
        "before_fix": dict(sorted(buckets.items())),
    }
    zero, pos = buckets.get("zero", 0), buckets.get("positive", 0)
    if county == "Collier":
        out["bad_zero"] = zero
        out["bad_positive_from_charge_text"] = pos + buckets.get("empty_raw_positive_numeric", 0)
        out["bad_text"] = buckets.get("text", 0)
        out["would_blank"] = out["bad_zero"] + out["bad_positive_from_charge_text"] + out["bad_text"]
    else:
        out["bad_zero"] = zero
        out["positive_unattributable"] = pos + buckets.get("empty_raw_positive_numeric", 0)
        out["text_unattributable"] = buckets.get("text", 0)
        out["would_blank"] = zero
        out["note"] = ("positive Glades bonds cannot be traced to their own card vs the next card from "
                       "stored fields (no per-charge bonds or card text are stored); not targeted")
    return out


def _valid_before(text: str) -> str:
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--before must be an ISO datetime: {exc}") from exc
    return text.rstrip("Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True, type=_valid_before,
                        help="UTC ISO time the #139 fix was deployed; later rows are not counted as bad")
    parser.add_argument("--print-filter", choices=COUNTIES,
                        help="print the affected-rows filter (Extended JSON for mongoexport/mongosh) and exit")
    args = parser.parse_args(argv)
    if args.print_filter:
        print(json.dumps(affected_filter(args.print_filter, args.before, extended_json=True)))
        return 0
    uri = os.getenv("MONGODB_URI", "")
    if not uri:
        print(json.dumps({"result": "error", "error": "MONGODB_URI is not set (run where Leads Ops has it)"}))
        return 1
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=8000).get_database(os.getenv("MONGODB_DB_NAME", "ShamrockBailDB"))
    out: Dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "before": args.before,
        "read_only": True,
    }
    for county in COUNTIES:
        docs = db["arrests"].find(county_clause(county), PROJECTION)
        out[county.lower()] = summarize(county, docs, args.before)
        out[county.lower()]["filter_matches"] = db["arrests"].count_documents(affected_filter(county, args.before))
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
