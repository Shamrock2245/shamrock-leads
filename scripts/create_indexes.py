#!/usr/bin/env python3
"""
ShamrockLeads — MongoDB Index Creation Script
==============================================
Run once on fresh deployment or after schema changes.
Idempotent: safe to re-run at any time.

Usage:
    python scripts/create_indexes.py

Requires MONGO_URI in environment (or .env file).
"""
import asyncio
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB_NAME", "shamrock_leads")


async def create_indexes():
    try:
        import motor.motor_asyncio
    except ImportError:
        print("ERROR: motor not installed. Run: pip install motor")
        sys.exit(1)

    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    print(f"Connected to MongoDB: {MONGO_URI} / {DB_NAME}")
    print("Creating indexes...\n")

    index_plan = {
        # ── active_bonds ──────────────────────────────────────────────────────
        "active_bonds": [
            ([("booking_number", 1)], {"unique": True, "name": "idx_booking_number_unique"}),
            ([("court_date", 1)], {"name": "idx_court_date"}),
            ([("status", 1)], {"name": "idx_status"}),
            ([("county", 1)], {"name": "idx_county"}),
            ([("status", 1), ("court_date", 1)], {"name": "idx_status_court_date"}),
            ([("defendant_name", 1)], {"name": "idx_defendant_name"}),
            ([("indemnitor_phone", 1)], {"name": "idx_indemnitor_phone"}),
            ([("poa_number", 1)], {"name": "idx_poa_number"}),
            ([("insurance_company", 1)], {"name": "idx_insurance_company"}),
            ([("created_at", -1)], {"name": "idx_created_at_desc"}),
        ],
        # ── court_reminders ───────────────────────────────────────────────────
        "court_reminders": [
            ([("booking_number", 1)], {"name": "idx_booking_number"}),
            ([("status", 1), ("send_at", 1)], {"name": "idx_status_send_at"}),
            ([("send_at", 1)], {"name": "idx_send_at"}),
            ([("reminder_type", 1), ("status", 1)], {"name": "idx_type_status"}),
        ],
        # ── arrests ───────────────────────────────────────────────────────────
        "arrests": [
            ([("booking_number", 1)], {"unique": True, "name": "idx_booking_number_unique"}),
            ([("defendant_name", 1)], {"name": "idx_defendant_name"}),
            ([("county", 1)], {"name": "idx_county"}),
            ([("arrest_date", -1)], {"name": "idx_arrest_date_desc"}),
            ([("charges", 1)], {"name": "idx_charges"}),
            ([("scraped_at", -1)], {"name": "idx_scraped_at_desc"}),
        ],
        # ── leads ─────────────────────────────────────────────────────────────
        "leads": [
            ([("booking_number", 1)], {"name": "idx_booking_number"}),
            ([("score", -1)], {"name": "idx_score_desc"}),
            ([("status", 1)], {"name": "idx_status"}),
            ([("county", 1)], {"name": "idx_county"}),
            ([("created_at", -1)], {"name": "idx_created_at_desc"}),
        ],
        # ── prospective_bonds ─────────────────────────────────────────────────
        "prospective_bonds": [
            ([("booking_number", 1)], {"name": "idx_booking_number"}),
            ([("status", 1)], {"name": "idx_status"}),
            ([("created_at", -1)], {"name": "idx_created_at_desc"}),
        ],
        # ── poa_inventory ─────────────────────────────────────────────────────
        "poa_inventory": [
            ([("poa_number", 1)], {"unique": True, "name": "idx_poa_number_unique"}),
            ([("status", 1)], {"name": "idx_status"}),
            ([("surety_id", 1), ("status", 1)], {"name": "idx_surety_status"}),
        ],
        # ── audit_events ──────────────────────────────────────────────────────
        "audit_events": [
            ([("entity_id", 1)], {"name": "idx_entity_id"}),
            ([("event_type", 1)], {"name": "idx_event_type"}),
            ([("timestamp", -1)], {"name": "idx_timestamp_desc"}),
        ],
        # ── defendants ────────────────────────────────────────────────────────
        "defendants": [
            ([("booking_number", 1)], {"name": "idx_booking_number"}),
            ([("phone", 1)], {"name": "idx_phone"}),
            ([("name", 1)], {"name": "idx_name"}),
        ],
        # ── discharge_queue ───────────────────────────────────────────────────
        "discharge_queue": [
            ([("status", 1)], {"name": "idx_status"}),
            ([("gmail_message_id", 1)], {"unique": True, "sparse": True, "name": "idx_gmail_msg_id"}),
            ([("created_at", -1)], {"name": "idx_created_at_desc"}),
        ],
        # ── geo_pings ─────────────────────────────────────────────────────────
        "geo_pings": [
            ([("booking_number", 1)], {"name": "idx_booking_number"}),
            ([("status", 1)], {"name": "idx_status"}),
            ([("token", 1)], {"unique": True, "name": "idx_token_unique"}),
        ],
    }

    total_created = 0
    total_skipped = 0
    total_errors = 0

    for collection_name, indexes in index_plan.items():
        col = db[collection_name]
        print(f"  Collection: {collection_name}")
        for keys, options in indexes:
            idx_name = options.get("name", "unnamed")
            try:
                result = await col.create_index(keys, **options)
                print(f"    ✓ {idx_name} → {result}")
                total_created += 1
            except Exception as e:
                err_str = str(e)
                if "already exists" in err_str or "IndexOptionsConflict" in err_str:
                    print(f"    ~ {idx_name} (already exists — skipped)")
                    total_skipped += 1
                else:
                    print(f"    ✗ {idx_name} ERROR: {e}")
                    total_errors += 1
        print()

    print("=" * 60)
    print(f"Done — created: {total_created}, skipped: {total_skipped}, errors: {total_errors}")
    client.close()


if __name__ == "__main__":
    asyncio.run(create_indexes())
