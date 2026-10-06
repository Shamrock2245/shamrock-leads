#!/usr/bin/env python3
"""
Hygiene script: Archive/void old test Shannon packets from August 2026.
Target packets: Lamb / Coulter / Ortiz test packets with no booking number and pending_staff_match: true.
Preserves Schmidt (1033474) and any real production packets. Idempotent.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from pymongo import MongoClient


def main() -> int:
    env_path = Path(
        os.environ.get(
            "SHAMROCK_LEADS_ENV",
            "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env",
        )
    )
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v

    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB_NAME") or "ShamrockBailDB"
    if not uri:
        print(json.dumps({"ok": False, "error": "MONGODB_URI_missing"}))
        return 1

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    now_iso = datetime.now(timezone.utc).isoformat()

    # Target: packets created in Aug 2026 with pending_staff_match: True and no booking number
    query = {
        "pending_staff_match": True,
        "$or": [
            {"booking_number": {"$in": [None, ""]}},
            {"booking_number": {"$exists": False}},
        ],
        "created_at": {"$regex": r"^2026-08"},
    }

    packets = list(db.paperwork_packets.find(query))
    archived = []

    for pkt in packets:
        pid = pkt.get("packet_id")
        def_name = pkt.get("defendant_name")
        res = db.paperwork_packets.update_one(
            {"_id": pkt["_id"]},
            {
                "$set": {
                    "pending_staff_match": False,
                    "status": "voided",
                    "voided": True,
                    "voided_at": now_iso,
                    "voided_by": "admin@shamrockbailbonds.biz",
                    "void_reason": "hygiene_archive_aug_2026_unbound_shannon_test",
                    "updated_at": now_iso,
                }
            },
        )
        archived.append({"packet_id": pid, "defendant_name": def_name, "modified": res.modified_count})

    print(json.dumps({"ok": True, "archived_count": len(archived), "packets": archived}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
