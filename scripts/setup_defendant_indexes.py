"""
setup_defendant_indexes.py — Phase 2 MongoDB Index Setup

Creates the required indexes for:
  - `defendants`   — identity_key (unique), full_name text, dob, counties, total_arrests
  - `audit_events` — entity_id, event_type, timestamp

Run once after deployment:
    python3 scripts/setup_defendant_indexes.py

Or call the /api/defendants/setup-indexes endpoint from the dashboard.
"""
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

if not MONGODB_URI:
    print("ERROR: MONGODB_URI not set in environment.")
    sys.exit(1)


def setup_indexes():
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    db = client[MONGODB_DB_NAME]

    # ── defendants collection ──────────────────────────────────────────────
    defendants = db["defendants"]

    # Unique identity key — prevents duplicate person records
    defendants.create_index(
        [("identity_key", ASCENDING)],
        unique=True,
        name="idx_identity_key_unique",
        background=True,
    )
    print("✅ defendants: idx_identity_key_unique")

    # defendant_id — primary lookup
    defendants.create_index(
        [("defendant_id", ASCENDING)],
        unique=True,
        name="idx_defendant_id_unique",
        background=True,
    )
    print("✅ defendants: idx_defendant_id_unique")

    # Full-text search on names
    defendants.create_index(
        [("full_name", TEXT), ("last_name", TEXT), ("first_name", TEXT)],
        name="idx_name_text",
        background=True,
    )
    print("✅ defendants: idx_name_text")

    # DOB — used for fuzzy matching
    defendants.create_index(
        [("dob", ASCENDING)],
        name="idx_dob",
        background=True,
    )
    print("✅ defendants: idx_dob")

    # Counties array — for county-based filtering
    defendants.create_index(
        [("counties", ASCENDING)],
        name="idx_counties",
        background=True,
    )
    print("✅ defendants: idx_counties")

    # Repeat offenders — sort by arrest count
    defendants.create_index(
        [("total_arrests", DESCENDING)],
        name="idx_total_arrests",
        background=True,
    )
    print("✅ defendants: idx_total_arrests")

    # Active flag — filter out tombstoned records
    defendants.create_index(
        [("active", ASCENDING)],
        name="idx_active",
        background=True,
    )
    print("✅ defendants: idx_active")

    # ── arrests collection — add defendant_id index ────────────────────────
    arrests = db["arrests"]
    arrests.create_index(
        [("defendant_id", ASCENDING)],
        name="idx_defendant_id",
        background=True,
        sparse=True,
    )
    print("✅ arrests: idx_defendant_id (sparse)")

    # ── audit_events collection ────────────────────────────────────────────
    audit = db["audit_events"]

    audit.create_index(
        [("entity_id", ASCENDING), ("timestamp", DESCENDING)],
        name="idx_entity_id_timestamp",
        background=True,
    )
    print("✅ audit_events: idx_entity_id_timestamp")

    audit.create_index(
        [("event_type", ASCENDING)],
        name="idx_event_type",
        background=True,
    )
    print("✅ audit_events: idx_event_type")

    audit.create_index(
        [("timestamp", DESCENDING)],
        name="idx_timestamp",
        background=True,
    )
    print("✅ audit_events: idx_timestamp")

    client.close()
    print("\n🎉 All Phase 2 indexes created successfully.")


if __name__ == "__main__":
    setup_indexes()
