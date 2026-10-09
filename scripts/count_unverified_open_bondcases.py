"""Count open BondCases that fail the Write Bond identity gate.

Read only. The connection string is ``SHAMROCK_MONGO_RO_URI`` and nothing else.
Prints counts only: no names, booking numbers, or ID numbers.

Do not run this against production from a cloud agent. A human runs it
with the read-only URI when they want the tally.

Open BondCase: status is ``active``, ``monitoring``, ``alert``, or
``reinstated`` (the kanban statuses that are not terminal). The same
bond case id in ``bond_cases`` and ``active_bonds`` counts once, and
``bond_cases`` wins. Synthetic ``TEST-`` / ``is_test`` rows are excluded.

Usage:
    SHAMROCK_MONGO_RO_URI='mongodb+srv://readonly... ' \\
        python scripts/count_unverified_open_bondcases.py
"""
from __future__ import annotations

import os
import sys

OPEN_STATUS_VALUES = (
    "active", "monitoring", "alert", "reinstated",
    "Active", "Monitoring", "Alert", "Reinstated",
    "ACTIVE", "MONITORING", "ALERT", "REINSTATED",
)

_SCAN_COLLECTIONS = (
    "intake_queue",
    "portal_pins",
    "paperwork_packets",
    "indemnitors",
    "prospective_bonds",
)


def _open_filter() -> dict:
    return {
        "$or": [
            {"status": {"$in": list(OPEN_STATUS_VALUES)}},
            {"Status": {"$in": list(OPEN_STATUS_VALUES)}},
        ]
    }


def _project() -> dict:
    return {
        "_id": 0,
        "status": 1,
        "Status": 1,
        "is_test": 1,
        "test_case": 1,
        "bond_case_id": 1,
        "Bond_Case_ID": 1,
        "booking_number": 1,
        "Booking_Number": 1,
        "packet_id": 1,
        "indemnitor_id": 1,
        "Indemnitor_ID": 1,
        "indemnitor_name": 1,
        "indemnitor": 1,
        "indemnitors": 1,
        "coindemnitor": 1,
        "co_indemnitor": 1,
        "id_extracted": 1,
        "id_scan": 1,
        "id_ocr": 1,
        "id_ocr_fields": 1,
        "id_ocr_role": 1,
        "license_scan": 1,
        "dl_scan": 1,
    }


def _print_counts(counts: dict[str, int]) -> None:
    for key in (
        "total_open",
        "verified_by_scan",
        "verified_by_attestation",
        "blocked_no_scan",
        "blocked_scan_failed",
        "blocked_name_mismatch",
        "blocked_lookup_failed",
    ):
        print(f"{key}={int(counts.get(key) or 0)}")


def _count(uri: str) -> int:
    from pymongo import MongoClient

    from dashboard.services.identity_verification_service import summarize_open_bond_cases

    db_name = os.environ.get("MONGODB_DB_NAME", "ShamrockBailDB").strip() or "ShamrockBailDB"
    client = MongoClient(
        uri,
        readPreference="secondaryPreferred",
        serverSelectionTimeoutMS=10000,
    )
    try:
        db = client[db_name]
        filt = _open_filter()
        projection = _project()
        cases = []
        for name in ("bond_cases", "active_bonds"):
            for doc in db[name].find(filt, projection):
                row = dict(doc)
                row["_collection"] = name
                cases.append(row)
        keys = []
        for row in cases:
            for field in ("bond_case_id", "Bond_Case_ID", "booking_number", "Booking_Number"):
                value = str(row.get(field) or "").strip()
                if value:
                    keys.append(value)
        related = []
        audits = []
        if keys:
            link = {"$or": [
                {"bond_case_id": {"$in": keys}},
                {"Bond_Case_ID": {"$in": keys}},
                {"booking_number": {"$in": keys}},
                {"Booking_Number": {"$in": keys}},
                {"indemnitor_id": {"$in": keys}},
                {"Indemnitor_ID": {"$in": keys}},
            ]}
            for name in _SCAN_COLLECTIONS:
                for doc in db[name].find(link, projection):
                    related.append(dict(doc))
            audit_filt = {"$and": [{"action": "staff_id_attestation"}, link]}
            for doc in db["audit_events"].find(audit_filt, {
                "_id": 0,
                "action": 1,
                "actor": 1,
                "timestamp": 1,
                "entity_id": 1,
                "details.indemnitor_name": 1,
                "details.role": 1,
                "details.id_type": 1,
                "details.issuing_state": 1,
                "details.id_last4": 1,
                "details.bond_case_id": 1,
                "details.booking_number": 1,
                "details.packet_id": 1,
                "details.indemnitor_id": 1,
                "details.timestamp": 1,
                "details.staff_user": 1,
            }):
                audits.append(dict(doc))
        counts = summarize_open_bond_cases(cases, related, audits)
    except Exception as exc:
        print(f"count failed closed error_type={type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        client.close()
    _print_counts(counts)
    return 0


def main() -> int:
    uri = os.environ.get("SHAMROCK_MONGO_RO_URI", "").strip()
    if not uri:
        print("SHAMROCK_MONGO_RO_URI is not set", file=sys.stderr)
        return 2
    return _count(uri)


if __name__ == "__main__":
    sys.exit(main())
