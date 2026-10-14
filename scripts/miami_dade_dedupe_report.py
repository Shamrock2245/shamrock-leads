#!/usr/bin/env python3
"""READ-ONLY Miami-Dade duplicate report for Leads Ops (2026-10-09).

Miami-Dade rows were keyed on the ArcGIS GlobalID/ObjectId, which the layer
reissues on every republish, so each republish stored the same booking again
under a new key. Runtime now keys rows on the internal natural key
(scrapers.counties.miami_dade.md_dedupe_key, owner exception 2026-10-09):

* sha256 of normalised defendant + DOB + BookDate (charges excluded), or
* without a DOB: defendant + BookDate + full verbatim SOURCE charges
  (``md_key_fallback``).

This script groups stored Miami-Dade arrests by that SAME function, so the
cleanup and runtime agree. A doc already written on the internal key uses its
stored key. Staff-edited charges are replaced by the saved source list
(``scraped_charges``, else ``staff_edits.charges.baseline``); a legacy staff
charge edit with neither (only matters for fallback keys) is counted as
unresolvable instead of being split from its duplicates.

Stored GlobalID rows written before 2026-10-09 carry no DOB, so they can only
get a fallback key here, while runtime gives the same booking a DOB key. Rows
whose BookDate is inside the runtime window are counted separately
(``rows_in_runtime_window_without_dob``): the cleanup must set their surviving
doc's key from the source DOB, or the next run stores them again.
``--with-source-dob`` reads the current public layer for that window (the same
read-only query the scraper runs) and counts how many of those rows map to
exactly one runtime key.

It only calls find()/count_documents() with a projection (and, with the
flag, the public ArcGIS query). It never writes, merges or deletes. Names,
DOBs, keys and hashes are never printed. Any cleanup needs a backup and
Brendan's OK first.

Usage (app env with MONGODB_URI / MONGODB_DB_NAME set):
    python scripts/miami_dade_dedupe_report.py [--with-source-dob]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.booking_identity import MD_INTERNAL_KEY_RE  # noqa: E402
from scrapers.counties.miami_dade import DAYS_BACK, _norm, _norm_charges, _norm_date, md_dedupe_key  # noqa: E402

COUNTY_VALUES = ["Miami-Dade", "Miami-Dade (FL)", "Miami Dade", "MIAMI-DADE"]
PROJECTION = {
    "_id": 0, "booking_number": 1, "full_name": 1, "dob": 1, "booking_date": 1, "charges": 1,
    "status": 1, "bond_amount_raw": 1, "staff_edits": 1, "bond_override": 1, "last_checked_mode": 1,
    "scraped_charges": 1, "md_dedupe": 1, "booking_key_internal": 1, "md_key_fallback": 1,
}
_GUID_RE = re.compile(r"^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$", re.I)


def key_shape(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return "blank"
    if MD_INTERNAL_KEY_RE.fullmatch(s):
        return "internal_natural_key"
    if _GUID_RE.match(s):
        return "arcgis_globalid"
    if s.isdigit():
        return "numeric (ArcGIS ObjectId)"
    return "other"


def _staff(doc: Dict[str, Any]) -> bool:
    return bool(doc.get("staff_edits") or doc.get("bond_override") or doc.get("last_checked_mode") == "MANUAL_CHARGE_BONDS")


def source_charges(doc: Dict[str, Any]) -> Optional[str]:
    """The charges string as the SOURCE published it, or None if unknowable."""
    if str(doc.get("scraped_charges") or "").strip():
        return str(doc["scraped_charges"])
    edits = doc.get("staff_edits") if isinstance(doc.get("staff_edits"), dict) else {}
    ch = edits.get("charges") if isinstance(edits.get("charges"), dict) else None
    if ch is not None:
        baseline = [str(b) for b in (ch.get("baseline") or []) if str(b).strip()]
        return " | ".join(baseline) if baseline else None
    if doc.get("last_checked_mode") == "MANUAL_CHARGE_BONDS":
        return None
    return str(doc.get("charges") or "")


def runtime_key(doc: Dict[str, Any]) -> Tuple[str, bool, str]:
    """(key, is_fallback, problem) using the runtime key function."""
    stored = str(doc.get("md_dedupe") or doc.get("booking_number") or "")
    if MD_INTERNAL_KEY_RE.fullmatch(stored):
        return stored, bool(doc.get("md_key_fallback")), ""
    if str(doc.get("dob") or "").strip():
        key, fb = md_dedupe_key(doc.get("full_name"), doc.get("dob"), doc.get("booking_date"))
        return key, fb, "" if key else "no_name_or_date"
    charges = source_charges(doc)
    if charges is None:
        return "", True, "staff_charges_unresolvable"
    key, fb = md_dedupe_key(doc.get("full_name"), "", doc.get("booking_date"), charges)
    return key, fb, "" if key else "no_name_or_date"


def _in_window(doc: Dict[str, Any], today: datetime) -> bool:
    d = _norm_date(doc.get("booking_date"))
    try:
        day = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return day >= today - timedelta(days=DAYS_BACK + 1)


def report(docs: Iterable[Dict[str, Any]], *, today: Optional[datetime] = None,
           source_index: Optional[Dict[Tuple[str, str, str], set]] = None) -> Dict[str, Any]:
    """Counts only. ``docs`` are arrests docs (projection above)."""
    today = today or datetime.now(timezone.utc)
    total = 0
    shapes: Counter = Counter()
    problems: Counter = Counter()
    keyed: Counter = Counter()
    window_no_dob = 0
    resolved: Counter = Counter()
    groups: Dict[str, list] = defaultdict(list)
    for d in docs:
        total += 1
        shapes[key_shape(d.get("booking_number"))] += 1
        key, fallback, problem = runtime_key(d)
        if problem:
            problems[problem] += 1
            continue
        keyed["fallback" if fallback else "dob_or_internal"] += 1
        if fallback and _in_window(d, today):
            window_no_dob += 1
            if source_index is not None:
                hits = source_index.get((_norm(d.get("full_name")), _norm_date(d.get("booking_date")),
                                         _norm_charges(source_charges(d) or "")), set())
                resolved["one_runtime_key" if len(hits) == 1 else ("ambiguous" if hits else "no_source_match")] += 1
        groups[key].append(d)
    dup = [g for g in groups.values() if len(g) > 1]
    sizes = Counter(len(g) for g in dup)
    out = {
        "total_rows": total,
        "booking_number_shapes": dict(shapes),
        "rows_keyed_dob_or_internal": keyed["dob_or_internal"],
        "rows_keyed_fallback": keyed["fallback"],
        "rows_without_name_or_date": problems["no_name_or_date"],
        "rows_staff_charges_unresolvable": problems["staff_charges_unresolvable"],
        "rows_in_runtime_window_without_dob": window_no_dob,
        "distinct_md_dedupe_keys": len(groups),
        "duplicate_groups": len(dup),
        "rows_in_duplicate_groups": sum(len(g) for g in dup),
        "rows_that_would_merge": sum(len(g) - 1 for g in dup),
        "duplicate_group_sizes": dict(sorted(sizes.items())),
        "groups_with_staff_edits": sum(1 for g in dup if any(_staff(d) for d in g)),
        "groups_with_differing_status": sum(1 for g in dup if len({str(d.get("status") or "") for d in g}) > 1),
        "groups_with_differing_bond_raw": sum(1 for g in dup if len({str(d.get("bond_amount_raw") or "") for d in g}) > 1),
    }
    if source_index is not None:
        out["window_rows_source_dob_lookup"] = dict(resolved)
    return out


def source_index_from_layer() -> Dict[Tuple[str, str, str], set]:
    """Read-only: the scraper's own public ArcGIS query for its window."""
    from scrapers.counties.miami_dade import MiamiDadeCountyScraper

    index: Dict[Tuple[str, str, str], set] = defaultdict(set)
    for r in MiamiDadeCountyScraper().scrape():
        index[(_norm(r.Full_Name), _norm_date(r.Booking_Date), _norm_charges(r.Charges))].add(r.extra_data["md_dedupe"])
    return index


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--with-source-dob", action="store_true",
                    help="also read the public layer (read-only) to resolve in-window rows without a DOB")
    args = ap.parse_args(argv)
    uri = os.getenv("MONGODB_URI")
    if not uri:
        sys.exit("MONGODB_URI is not set (run inside the app env)")
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]
    by_county = {c: db.arrests.count_documents({"county": c}) for c in COUNTY_VALUES}
    print("rows per stored county value:", by_county)
    index = source_index_from_layer() if args.with_source_dob else None
    out = report(db.arrests.find({"county": {"$in": COUNTY_VALUES}}, PROJECTION), source_index=index)
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
