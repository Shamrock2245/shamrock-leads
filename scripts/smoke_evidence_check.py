#!/usr/bin/env python3
"""Read-only write-smoke evidence for one county (aggregates only, no names).

Run by Leads Ops wherever ``MONGODB_URI`` is set (the agent box has none):

    python scripts/smoke_evidence_check.py --county Lee --state FL --hours 24

It reads ``arrests`` rows for that county written in the window and prints one
JSON line: row count, booking-key shapes and uniqueness, how many rows have an
empty, zero or positive bond, charges, booking date/time, release fields,
mugshot, DOB and address. For internal-key scopes (Manatee Clerk, Miami-Dade)
it also counts rows flagged ``booking_key_internal``, keys that match the
scope's exact pattern, case / OBTS numbers present, and any row whose stored
booking number equals its case number. It never prints a name, DOB, address,
booking, case or OBTS number, and it never writes. Paste the JSON into the ``result`` of the
matching ``docs/recon/smoke_evidence.json`` row (see
docs/recon/FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.booking_identity import MC_INTERNAL_KEY_RE, MD_INTERNAL_KEY_RE  # noqa: E402  (pure: re/typing only)

_INTERNAL_KEY_RES = {"mc_case_v1": MC_INTERNAL_KEY_RE, "md_dedupe_v2": MD_INTERNAL_KEY_RE}


def internal_key_family(value: Any) -> str:
    """``mc_case_v1`` / ``md_dedupe_v2`` when the value is a valid internal key, else ""."""
    text = str(value or "")
    for family, pattern in _INTERNAL_KEY_RES.items():
        if pattern.fullmatch(text):
            return family
    return ""


def key_shape(value: Any) -> str:
    """``1033660`` -> ``NNNNNNN``; ``GCSO26JBN000123`` -> ``AAAANNAAANNNNNN``.

    A valid internal key prints as ``mc_case_v1:<sha256>`` (one shape, no digest).
    """
    family = internal_key_family(value)
    if family:
        return f"{family}:<sha256>"
    return re.sub(r"\d", "N", re.sub(r"[A-Za-z]", "A", str(value or "")))


def _case_token(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def mc_case_digest(case_number: Any) -> str:
    """``mc_case_v1:`` plus the sha256 of the normalised case number, or ``""``.

    Same rule as ``scrapers.counties.manatee_clerk.mc_case_key``: upper-case,
    then drop every character that is not A-Z or 0-9. The digest is never printed.
    """
    case = _case_token(case_number)
    if not case:
        return ""
    return "mc_case_v1:" + hashlib.sha256(case.encode("utf-8")).hexdigest()


def _key_matches_case(doc: dict) -> bool:
    """True when this row's stored key is the digest of its own case number."""
    booking = str(doc.get("booking_number") or "")
    if internal_key_family(booking) != "mc_case_v1":
        return False
    derived = mc_case_digest(doc.get("case_number"))
    if not derived or len(derived) != len(booking):
        return False
    return hmac.compare_digest(derived, booking)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _status_bucket(value: Any) -> str:
    """Blank only for None or a blank string.

    ``0``, ``False``, ``[]`` and ``{}`` are falsy but they are not an unknown
    custody status. They count as ``malformed`` so the Manatee gate, which
    requires exactly ``{"": rows}``, fails closed.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return "malformed"


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
        "status": dict(Counter(_status_bucket(d.get("status")) for d in rows).most_common(6)),
        "with_release_date": sum(1 for d in rows if not _blank(d.get("release_date"))),
        "with_mugshot": sum(1 for d in rows if not _blank(d.get("mugshot_url"))),
        "with_dob": sum(1 for d in rows if not _blank(d.get("dob"))),
        "with_address": sum(1 for d in rows if not _blank(d.get("address"))),
        "booking_key_internal": sum(1 for d in rows if d.get("booking_key_internal") is True),
        "internal_key_valid": sum(1 for k in keys if internal_key_family(k)),
        "internal_key_matches_case": sum(1 for d in rows if _key_matches_case(d)),
        "with_case_number": sum(1 for d in rows if not _blank(d.get("case_number"))),
        "with_obts_number": sum(1 for d in rows if not _blank(d.get("obts_number"))),
        "booking_equals_case_number": sum(
            1 for d in rows
            if _case_token(d.get("case_number")) and _case_token(d.get("case_number")) == _case_token(d.get("booking_number"))
        ),
        "first_scraped_at": scraped[0] if scraped else "",
        "last_scraped_at": scraped[-1] if scraped else "",
    }


# Per-source identity gates (Codex 11/4 on #308). A source whose stored key
# is internal (not a jail booking number) must prove it before a smoke can
# pass. Keyed by the smoke_evidence.json label.
_COUNTER_KEYS = (
    "rows", "booking_number_blank", "booking_number_duplicates", "booking_key_internal",
    "internal_key_valid", "internal_key_matches_case", "with_case_number",
    "booking_equals_case_number", "with_charges", "with_booking_date",
)


def _int(result: dict, key: str) -> int | None:
    value = result.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


MC_SHAPE = "mc_case_v1:<sha256>"


def manatee_clerk_identity_problems(result: dict) -> list[str]:
    """Pass criteria from FL_MANATEE_CLERK_SCRAPER_2026-10-10.md; [] means pass.

    Missing or non-integer counters fail (fail closed). Manatee Clerk may only
    use the ``mc_case_v1`` family (Codex 11/5): a valid Miami-Dade
    ``md_dedupe_v2`` key must not certify this smoke. ``status_buckets`` must be
    exactly ``{"": rows}`` with an integer count (Codex 11/5).
    """
    if not isinstance(result, dict):
        return ["result missing"]
    vals = {k: _int(result, k) for k in _COUNTER_KEYS}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return [f"missing counter: {k}" for k in missing]
    rows = vals["rows"]
    problems = []
    if rows < 1:
        problems.append("rows < 1")
    for key in ("booking_number_blank", "booking_number_duplicates", "booking_equals_case_number",
                "with_booking_date"):
        if vals[key] != 0:
            problems.append(f"{key} != 0")
    for key in ("booking_key_internal", "internal_key_valid", "internal_key_matches_case",
                "with_case_number", "with_charges"):
        if vals[key] != rows:
            problems.append(f"{key} != rows")
    shapes = result.get("booking_number_shapes")
    if not isinstance(shapes, dict) or set(shapes) != {MC_SHAPE} or _int(shapes, MC_SHAPE) != rows:
        # Wrong family (e.g. md_dedupe_v2), mixed shapes, or count != rows.
        problems.append(f"booking_number_shapes != {{{MC_SHAPE}: rows}}")
    buckets = result.get("status_buckets")
    if (
        not isinstance(buckets, dict)
        or set(buckets) != {""}
        or _int(buckets, "") != rows
    ):
        problems.append('status_buckets != {"": rows}')
    problems.extend(_bond_bucket_problems(result.get("bond"), rows))
    return problems


# Bonds the Clerk smoke may certify: unknown, a listed $0.00 row, or a
# positive amount from the Bonds table. ``negative`` and ``text`` are not.
_BOND_OK = frozenset({"empty", "zero", "positive"})


def _bond_bucket_problems(bond: Any, rows: int) -> list[str]:
    """Problems when the bond aggregate is not empty, zero, or positive."""
    if not isinstance(bond, dict) or not bond:
        return ["bond buckets missing"]
    if set(bond) - _BOND_OK:
        return ["bond buckets not empty/zero/positive"]
    total = 0
    for key in bond:
        count = _int(bond, key)
        if count is None or count < 0:
            return ["bond bucket count invalid"]
        total += count
    if total != rows:
        return ["bond buckets != rows"]
    return []


IDENTITY_GATES = {
    "Manatee Clerk (FL)": manatee_clerk_identity_problems,
}


def canonical_state(state: str) -> str:
    """``Florida`` and ``FL`` are the same gate state. Other text is only stripped."""
    st = " ".join(str(state or "").split()).upper()
    if st in {"FL", "FLORIDA"}:
        return "FL"
    return st


def _canonical_gate_label(label: str) -> str:
    """Match an identity-gate key ignoring case and extra whitespace.

    ``county_query`` already strips the county and matches it case-insensitively.
    The gate lookup must use the same spelling, so ``manatee clerk`` still
    selects ``Manatee Clerk (FL)``.
    """
    text = " ".join(str(label or "").split())
    def _paren(match: re.Match[str]) -> str:
        return "(" + canonical_state(match.group(1)) + ")"

    text = re.sub(r"\(\s*([^)]*?)\s*\)", _paren, text)
    folded = text.casefold()
    for key in IDENTITY_GATES:
        if " ".join(key.split()).casefold() == folded:
            return key
    return " ".join(str(label or "").split())


def evidence_label(county: str, state: str) -> str:
    """``County (ST)`` with whitespace stripped from both parts."""
    name = " ".join(str(county or "").split())
    return f"{name} ({canonical_state(state)})"


def identity_gate(label: str, result: dict) -> list[str] | None:
    """None when the label has no identity gate, else the problem list."""
    gate = IDENTITY_GATES.get(_canonical_gate_label(label))
    return None if gate is None else gate(result)


def county_query(county: str, state: str, since: datetime) -> dict:
    """Same county/state rule as the lead list; Florida owns rows with no state."""
    name = re.escape(county.strip())
    st = canonical_state(state)
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
    "booking_key_internal": 1, "case_number": 1, "obts_number": 1,
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
        "label": evidence_label(args.county, args.state),
        "window_hours": args.hours,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **summarize(docs),
    }
    out["status_buckets"] = out["status"]
    out["result"] = "ok" if out["rows"] else "no_rows"
    problems = identity_gate(out["label"], out) if out["rows"] else None
    if problems is not None:
        out["identity_gate"] = "fail" if problems else "pass"
        out["identity_problems"] = problems
        if problems:
            out["result"] = "identity_failed"
    print(json.dumps(out, sort_keys=True))
    if not out["rows"]:
        return 2
    return 3 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
