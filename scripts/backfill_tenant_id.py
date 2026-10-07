#!/usr/bin/env python3
"""Stamp tenant_id='shamrock' on tenant-owned documents.

Default is an offline plan: no socket, no writes. Counting rows requires
``--connect``. Writing requires ``--apply`` and
``SAAS_TENANT_BACKFILL_I_UNDERSTAND=1``.

Do not run --apply against production from a cloud agent. The tests exercise
the planner and a fake database only.

Down path: ``--down`` unsets tenant_id only on documents this script stamped
(tenant_id=shamrock and tenant_backfill_rev=1). Documents that already had a
tenant id, or that the app stamped later without the rev marker, are left alone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python scripts/backfill_tenant_id.py` from the repo root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dashboard.tenancy.constants import (  # noqa: E402
    BACKFILL_REV,
    BACKFILL_REV_FIELD,
    GLOBAL_COLLECTIONS,
    PLATFORM_COLLECTIONS,
    SHAMROCK_TENANT_ID,
    TENANT_FIELD,
    TENANT_OWNED_COLLECTIONS,
)
from dashboard.tenancy.indexes import tenant_index_specs  # noqa: E402
from dashboard.tenancy.model import (  # noqa: E402
    shamrock_owner_membership,
    shamrock_tenant_document,
)

ACK_ENV = "SAAS_TENANT_BACKFILL_I_UNDERSTAND"

MISSING_TENANT = {
    "$or": [
        {TENANT_FIELD: {"$exists": False}},
        {TENANT_FIELD: None},
        {TENANT_FIELD: ""},
    ]
}

STAMPED_BY_THIS_SCRIPT = {
    TENANT_FIELD: SHAMROCK_TENANT_ID,
    BACKFILL_REV_FIELD: BACKFILL_REV,
}


def acknowledge_enabled() -> bool:
    return os.getenv(ACK_ENV, "").strip() == "1"


def offline_plan() -> dict:
    specs = tenant_index_specs()
    return {
        "connected": False,
        "mode": "offline",
        "tenant_id": SHAMROCK_TENANT_ID,
        "would_touch_collections": sorted(TENANT_OWNED_COLLECTIONS),
        "global_untouched": sorted(GLOBAL_COLLECTIONS),
        "platform_seed_only": sorted(PLATFORM_COLLECTIONS),
        "seed_tenant": shamrock_tenant_document(),
        "seed_membership": shamrock_owner_membership(),
        "indexes_defined_not_applied": [
            {"collection": spec.collection, "name": spec.name, "keys": list(spec.keys)}
            for spec in specs
        ],
        "note": (
            "No database connection. --connect counts missing tenant_id rows. "
            "--apply writes only when SAAS_TENANT_BACKFILL_I_UNDERSTAND=1."
        ),
    }


def _collection_names(db) -> list[str]:
    names = getattr(db, "list_collection_names", None)
    if callable(names):
        return list(names())
    if isinstance(db, dict):
        return list(db.keys())
    raise TypeError("database handle cannot list collections")


def _get_collection(db, name: str):
    if isinstance(db, dict):
        return db[name]
    return db[name]


def summarize_collection(collection) -> dict:
    missing = collection.count_documents(MISSING_TENANT)
    stamped = collection.count_documents(STAMPED_BY_THIS_SCRIPT)
    return {"missing_tenant_id": int(missing), "already_stamped": int(stamped)}


def apply_collection(collection, *, dry_run: bool) -> dict:
    summary = summarize_collection(collection)
    summary["updated"] = 0
    if dry_run:
        summary["would_update"] = summary["missing_tenant_id"]
        return summary
    result = collection.update_many(
        MISSING_TENANT,
        {"$set": {TENANT_FIELD: SHAMROCK_TENANT_ID, BACKFILL_REV_FIELD: BACKFILL_REV}},
    )
    summary["updated"] = int(getattr(result, "modified_count", 0))
    summary["would_update"] = 0
    return summary


def down_collection(collection, *, dry_run: bool) -> dict:
    count = int(collection.count_documents(STAMPED_BY_THIS_SCRIPT))
    if dry_run:
        return {"would_unset": count, "unset": 0}
    result = collection.update_many(
        STAMPED_BY_THIS_SCRIPT,
        {"$unset": {TENANT_FIELD: "", BACKFILL_REV_FIELD: ""}},
    )
    return {"would_unset": 0, "unset": int(getattr(result, "modified_count", 0))}


def seed_directory(db, *, dry_run: bool) -> dict:
    """Upsert tenant #1 and its owner membership. Does not stamp other tenants."""
    if dry_run:
        return {"would_seed": ["tenants", "tenant_memberships"], "seeded": []}
    tenants = _get_collection(db, "tenants")
    members = _get_collection(db, "tenant_memberships")
    tenant_doc = shamrock_tenant_document()
    member_doc = shamrock_owner_membership()
    tenants.update_one(
        {"tenant_id": SHAMROCK_TENANT_ID},
        {"$setOnInsert": tenant_doc},
        upsert=True,
    )
    members.update_one(
        {"tenant_id": SHAMROCK_TENANT_ID, "email": member_doc["email"]},
        {"$setOnInsert": member_doc},
        upsert=True,
    )
    return {"would_seed": [], "seeded": ["tenants", "tenant_memberships"]}


def run_against(db, *, dry_run: bool, down: bool) -> dict:
    present = set(_collection_names(db))
    report = {
        "connected": True,
        "mode": "down-dry-run" if down and dry_run else "down" if down else "dry-run" if dry_run else "apply",
        "tenant_id": SHAMROCK_TENANT_ID,
        "collections": {},
        "skipped_missing": [],
    }
    for name in sorted(TENANT_OWNED_COLLECTIONS):
        if name not in present:
            report["skipped_missing"].append(name)
            continue
        collection = _get_collection(db, name)
        if down:
            report["collections"][name] = down_collection(collection, dry_run=dry_run)
        else:
            report["collections"][name] = apply_collection(collection, dry_run=dry_run)
    if not down:
        report["seed"] = seed_directory(db, dry_run=dry_run)
    return report


def assert_write_allowed(apply: bool) -> None:
    if apply and not acknowledge_enabled():
        raise SystemExit(
            f"Refusing to write. Set {ACK_ENV}=1 after a human maintenance window. "
            "This script does not run against production unless that ack is set."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run Shamrock tenant_id backfill")
    parser.add_argument("--connect", action="store_true", help="Open Mongo and count rows")
    parser.add_argument("--apply", action="store_true", help="Write stamps. Requires the ack env.")
    parser.add_argument("--down", action="store_true", help="Unset stamps this script created")
    args = parser.parse_args(argv)

    if not args.connect:
        if args.apply or args.down:
            raise SystemExit("--apply and --down require --connect, and still require the ack env.")
        print(json.dumps(offline_plan(), indent=2, default=str))
        return 0

    writing = bool(args.apply)
    assert_write_allowed(writing or bool(args.down and writing))
    # --down without --apply is a dry-run of the down path even when connected.
    dry_run = not args.apply
    if args.down and args.apply:
        assert_write_allowed(True)

    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise SystemExit("MONGODB_URI is not set. Offline plan does not need it.")
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
        report = run_against(client[db_name], dry_run=dry_run, down=args.down)
    finally:
        client.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
