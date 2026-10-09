#!/usr/bin/env python3
"""READ-ONLY Miami-Dade duplicate report for Leads Ops (2026-10-09).

Miami-Dade rows were keyed on the ArcGIS GlobalID/ObjectId, which the layer
reissues on every republish, so each republish stored the same booking again
under a new key. This script groups stored Miami-Dade arrests by the new
internal ``md_dedupe`` key (scrapers.counties.miami_dade.md_dedupe_key:
defendant | booking date | full charges) and prints COUNTS ONLY:
total rows, key shapes, duplicate groups, and rows that would merge.

It only calls find() with a projection. It never writes, merges or deletes.
Names, keys and hashes are never printed. Any cleanup needs a backup and
Brendan's OK first.

Usage (app env with MONGODB_URI / MONGODB_DB_NAME set):
    python scripts/miami_dade_dedupe_report.py
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.counties.miami_dade import md_dedupe_key  # noqa: E402

COUNTY_VALUES = ["Miami-Dade", "Miami-Dade (FL)", "Miami Dade", "MIAMI-DADE"]
PROJECTION = {
    "_id": 0, "booking_number": 1, "full_name": 1, "booking_date": 1, "charges": 1,
    "status": 1, "bond_amount_raw": 1, "staff_edits": 1, "bond_override": 1, "last_checked_mode": 1,
}
_GUID_RE = re.compile(r"^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$", re.I)


def key_shape(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return "blank"
    if _GUID_RE.match(s):
        return "arcgis_globalid"
    if s.isdigit():
        return "numeric (ArcGIS ObjectId)"
    return "other"


def _staff(doc: Dict[str, Any]) -> bool:
    return bool(doc.get("staff_edits") or doc.get("bond_override") or doc.get("last_checked_mode") == "MANUAL_CHARGE_BONDS")


def report(docs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts only. ``docs`` are arrests docs (projection above)."""
    total = 0
    shapes: Counter = Counter()
    unkeyable = 0
    groups: Dict[str, list] = defaultdict(list)
    for d in docs:
        total += 1
        shapes[key_shape(d.get("booking_number"))] += 1
        k = md_dedupe_key(d.get("full_name"), d.get("booking_date"), d.get("charges"))
        if not k:
            unkeyable += 1
            continue
        groups[k].append(d)
    dup = [g for g in groups.values() if len(g) > 1]
    sizes = Counter(len(g) for g in dup)
    return {
        "total_rows": total,
        "booking_number_shapes": dict(shapes),
        "rows_without_name_or_date": unkeyable,
        "distinct_md_dedupe_keys": len(groups),
        "duplicate_groups": len(dup),
        "rows_in_duplicate_groups": sum(len(g) for g in dup),
        "rows_that_would_merge": sum(len(g) - 1 for g in dup),
        "duplicate_group_sizes": dict(sorted(sizes.items())),
        "groups_with_staff_edits": sum(1 for g in dup if any(_staff(d) for d in g)),
        "groups_with_differing_status": sum(1 for g in dup if len({str(d.get("status") or "") for d in g}) > 1),
        "groups_with_differing_bond_raw": sum(1 for g in dup if len({str(d.get("bond_amount_raw") or "") for d in g}) > 1),
    }


def main() -> None:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        sys.exit("MONGODB_URI is not set (run inside the app env)")
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]
    by_county = {c: db.arrests.count_documents({"county": c}) for c in COUNTY_VALUES}
    print("rows per stored county value:", by_county)
    out = report(db.arrests.find({"county": {"$in": COUNTY_VALUES}}, PROJECTION))
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
