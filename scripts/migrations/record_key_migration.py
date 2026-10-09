#!/usr/bin/env python3
"""record_key migration (DRAFT): Miami-Dade booking_number can be stored blank.

DRY-RUN BY DEFAULT. Nothing is written unless every apply guard passes.
Not to be run against prod until Leads Ops' Miami-Dade cleanup is done and
Brendan has approved (data change on prod).

What it does (core/record_key.py):
  1. Backfill ``record_key`` on every arrests doc (booking_number, or the
     internal md_dedupe key for internal-key records).
  2. Duplicate check on (state, county, record_key) and on non-empty
     (state, county, booking_number). Any duplicate aborts --apply.
  3. Create unique index (state, county, record_key); create the partial
     unique index on non-empty booking_number; drop the legacy full unique
     index ``dedup_state_county_booking``.
  4. Internal-key records (Miami-Dade): set ``booking_number=""``; the key
     stays in ``record_key`` / ``md_dedupe``.
  5. Linked collections: set ``record_key`` on docs whose booking reference
     is an internal key. Their ``booking_number`` is only blanked with
     ``--blank-linked`` (a later, separate step once linked routes use
     ``arrest_ref_query``).
  After apply, ops sets ``RECORD_KEY_MODE=on`` for the scraper and dashboard.

BACKUP (required before --apply; the script checks it exists):
  mongodump --uri "$MONGODB_URI" --db "$MONGODB_DB_NAME" \
      --collection arrests --out /backups/record_key_$(date +%Y%m%d_%H%M)
  # repeat --collection for each linked collection below, same --out
  # restore: mongorestore --uri "$MONGODB_URI" --nsInclude "$DB.arrests" --drop <dir>

Usage (app env with MONGODB_URI / MONGODB_DB_NAME):
  python scripts/migrations/record_key_migration.py                 # dry run, report only
  python scripts/migrations/record_key_migration.py --report out.json
  python scripts/migrations/record_key_migration.py --apply \
      --backup-dir /backups/record_key_20261012_0900 --i-have-a-verified-backup

Reports carry counts, collection/field names and ObjectIds only, never names,
DOBs or keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.booking_identity import is_internal_booking_key  # noqa: E402
from core.record_key import (  # noqa: E402
    BOOKING_PARTIAL_INDEX,
    LEGACY_BOOKING_INDEX,
    RECORD_KEY_INDEX,
    record_key_for,
)

ARRESTS = "arrests"
# (collection, booking-reference fields). Mirrors admin_hygiene's map plus
# the dashboard lists that link by booking number.
LINKED: List[tuple] = [
    ("leads", ("booking_number",)),
    ("matches", ("booking_number",)),
    ("intake_queue", ("booking_number", "matched_booking_number")),
    ("prospective_bonds", ("booking_number",)),
    ("active_bonds", ("booking_number",)),
    ("defendant_notes", ("booking_number",)),
    ("court_reminders", ("booking_number",)),
    ("paperwork_packets", ("booking_number",)),
    ("payments", ("booking_number",)),
    ("tasks", ("booking_number",)),
    ("relationships", ("booking_number",)),
    ("indemnitors", ("booking_number",)),
    ("recovery_shares", ("booking_number",)),
    ("notifications", ("booking_number",)),
]
ARREST_PROJECTION = {"_id": 1, "state": 1, "county": 1, "booking_number": 1, "record_key": 1,
                     "md_dedupe": 1, "booking_key_internal": 1}
BATCH = 500


def _scope(doc: Dict[str, Any]) -> tuple:
    return (str(doc.get("state") or "FL").upper(), str(doc.get("county") or ""))


def _oid(doc: Dict[str, Any]) -> str:
    return str(doc.get("_id"))


def _is_internal(doc: Dict[str, Any]) -> bool:
    return bool(doc.get("booking_key_internal")) or is_internal_booking_key(doc.get("booking_number")) \
        or is_internal_booking_key(record_key_for(doc))


def plan(arrest_docs: Iterable[Dict[str, Any]], linked_counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Pure dry-run plan + duplicate report from arrests docs."""
    by_rk: Dict[tuple, List[str]] = defaultdict(list)
    by_bk: Dict[tuple, List[str]] = defaultdict(list)
    backfill = internal = no_identity = 0
    for d in arrest_docs:
        rk = record_key_for(d)
        if not rk:
            no_identity += 1
            continue
        if str(d.get("record_key") or "") != rk:
            backfill += 1
        by_rk[_scope(d) + (rk,)].append(_oid(d))
        if _is_internal(d):
            internal += 1
        else:
            bk = str(d.get("booking_number") or "").strip()
            if bk:
                by_bk[_scope(d) + (bk,)].append(_oid(d))
    dup_rk = [{"state": k[0], "county": k[1], "count": len(v), "ids": v} for k, v in by_rk.items() if len(v) > 1]
    dup_bk = [{"state": k[0], "county": k[1], "count": len(v), "ids": v} for k, v in by_bk.items() if len(v) > 1]
    return {
        "mode": "plan",
        "arrests_with_identity": sum(len(v) for v in by_rk.values()),
        "arrests_without_identity": no_identity,
        "record_key_backfill": backfill,
        "internal_key_records_to_blank": internal,
        "duplicate_record_keys": dup_rk,
        "duplicate_nonempty_booking_numbers": dup_bk,
        "linked_internal_refs": dict(linked_counts or {}),
        "safe_to_apply": not dup_rk and not dup_bk,
    }


def linked_internal_counts(db: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for coll, fields in LINKED:
        for f in fields:
            q = {f: {"$regex": r"^md_dedupe_v\d+:[0-9a-f]{16,64}$"}}
            try:
                n = db[coll].count_documents(q)
            except Exception:
                n = -1  # collection unreadable; reported, not fatal for a dry run
            if n:
                out[f"{coll}.{f}"] = n
    return out


def check_backup(backup_dir: Optional[str], db_name: str) -> Optional[str]:
    """Error message, or None when a mongodump of arrests is present."""
    if not backup_dir:
        return "--backup-dir is required for --apply"
    p = Path(backup_dir) / db_name / f"{ARRESTS}.bson"
    if not p.is_file() or p.stat().st_size == 0:
        return f"backup not found: {p} (run the mongodump in this script's docstring first)"
    return None


def _bulk(coll: Any, ops: List[Any]) -> int:
    if not ops:
        return 0
    coll.bulk_write(ops, ordered=False)
    return len(ops)


def apply(db: Any, *, blank_linked: bool = False, update_one_cls: Any = None) -> Dict[str, Any]:
    """Run steps 1-5. Caller has already passed every guard (backup + flags + clean plan)."""
    if update_one_cls is None:
        from pymongo import UpdateOne as update_one_cls  # noqa: N813
    arrests = db[ARRESTS]
    done = {"record_key_backfilled": 0, "internal_blanked": 0, "linked_record_key_set": 0, "linked_blanked": 0}
    # 1. backfill record_key
    ops: List[Any] = []
    for d in arrests.find({}, ARREST_PROJECTION):
        rk = record_key_for(d)
        if rk and str(d.get("record_key") or "") != rk:
            ops.append(update_one_cls({"_id": d["_id"]}, {"$set": {"record_key": rk}}))
        if len(ops) >= BATCH:
            done["record_key_backfilled"] += _bulk(arrests, ops)
            ops = []
    done["record_key_backfilled"] += _bulk(arrests, ops)
    # 3. indexes: record_key unique first, then swap the booking index
    arrests.create_index([("state", 1), ("county", 1), ("record_key", 1)], unique=True, name=RECORD_KEY_INDEX)
    arrests.create_index([("state", 1), ("county", 1), ("booking_number", 1)], unique=True,
                         name=BOOKING_PARTIAL_INDEX, partialFilterExpression={"booking_number": {"$gt": ""}})
    if LEGACY_BOOKING_INDEX in (arrests.index_information() or {}):
        arrests.drop_index(LEGACY_BOOKING_INDEX)
    # 4. blank booking_number on internal-key records
    ops = []
    internal_keys: set = set()
    for d in arrests.find({}, ARREST_PROJECTION):
        if _is_internal(d):
            internal_keys.add(record_key_for(d))
            if d.get("booking_number"):
                ops.append(update_one_cls({"_id": d["_id"]},
                                          {"$set": {"booking_number": "", "booking_key_internal": True}}))
    done["internal_blanked"] = _bulk(arrests, ops)
    # 5. linked collections
    for coll, fields in LINKED:
        c = db[coll]
        for f in fields:
            ops = []
            for d in c.find({f: {"$in": sorted(internal_keys)}}, {"_id": 1, f: 1}):
                s: Dict[str, Any] = {"record_key": d[f]} if f == "booking_number" else {f"{f}_record_key": d[f]}
                if blank_linked:
                    s[f] = ""
                ops.append(update_one_cls({"_id": d["_id"]}, {"$set": s}))
            n = _bulk(c, ops)
            done["linked_record_key_set"] += n
            if blank_linked:
                done["linked_blanked"] += n
    return done


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--backup-dir", help="mongodump output dir (must contain <db>/arrests.bson)")
    ap.add_argument("--i-have-a-verified-backup", action="store_true", dest="verified")
    ap.add_argument("--blank-linked", action="store_true", help="also blank linked booking_number (later step)")
    ap.add_argument("--report", help="write the JSON report here")
    args = ap.parse_args(argv)

    if args.apply and not args.verified:
        print("refusing --apply without --i-have-a-verified-backup", file=sys.stderr)
        return 2
    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    if args.apply:
        err = check_backup(args.backup_dir, db_name)
        if err:
            print(f"refusing --apply: {err}", file=sys.stderr)
            return 2
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("MONGODB_URI is not set (run inside the app env)", file=sys.stderr)
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[db_name]
    report = plan(db[ARRESTS].find({}, ARREST_PROJECTION), linked_internal_counts(db))
    if args.apply:
        if not report["safe_to_apply"]:
            report["mode"] = "aborted_duplicates"
        else:
            report["mode"] = "applied"
            report["applied"] = apply(db, blank_linked=args.blank_linked)
    text = json.dumps(report, indent=2, default=str)
    if args.report:
        Path(args.report).write_text(text)
    print(text)
    return 0 if report["mode"] != "aborted_duplicates" else 3


if __name__ == "__main__":
    sys.exit(main())
