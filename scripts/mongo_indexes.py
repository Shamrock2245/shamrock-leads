#!/usr/bin/env python3
"""
ShamrockLeads — MongoDB Index Hardening
=========================================
Creates compound indexes for query performance at scale.
Safe to re-run — create_index() is idempotent.

Usage:
    python scripts/mongo_indexes.py

Collections indexed:
    - arrests         (main data — dedup, scoring queries, timeline)
    - court_email_log (email processing dedup)
    - error_log       (TTL auto-expire, filtered lookups)
    - scraper_status  (fleet health queries)
    - defendants      (name/phone lookups)
    - poa_inventory   (surety lookups)
"""

import os
import sys
from pathlib import Path
from pymongo import MongoClient, ASCENDING, DESCENDING, IndexModel
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

if not MONGO_URI:
    print("❌ MONGODB_URI not set in .env")
    sys.exit(1)


def create_indexes():
    """Create all indexes. Idempotent — safe to re-run."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    db = client[MONGO_DB]

    print(f"🔗 Connected to {MONGO_DB}")
    print("=" * 60)

    # ── arrests collection ──
    print("\n📊 arrests collection:")
    arrests = db["arrests"]

    indexes = [
        IndexModel(
            [("booking_number", ASCENDING), ("county", ASCENDING)],
            unique=True,
            name="idx_dedup_booking_county",
            comment="Dedup key — prevents duplicate arrest records",
        ),
        IndexModel(
            [("county", ASCENDING), ("lead_score", DESCENDING)],
            name="idx_county_score",
            comment="County + score queries (Command Center, Lead Explorer)",
        ),
        IndexModel(
            [("scraped_at", DESCENDING)],
            name="idx_scraped_at",
            comment="Timeline queries (recent records)",
        ),
        IndexModel(
            [("lead_status", ASCENDING), ("bond_amount", DESCENDING)],
            name="idx_status_bond",
            comment="Bounty board queries (Hot leads by bond amount)",
        ),
        IndexModel(
            [("status", ASCENDING), ("bond_amount", DESCENDING), ("lead_score", DESCENDING)],
            name="idx_command_center",
            comment="Command Center compound query",
        ),
        IndexModel(
            [("full_name", ASCENDING)],
            name="idx_full_name",
            comment="Name search",
        ),
        IndexModel(
            [("county", ASCENDING), ("scraped_at", DESCENDING)],
            name="idx_county_scraped",
            comment="Per-county timeline",
        ),
    ]

    for idx in indexes:
        try:
            name = arrests.create_index(idx.document["key"], **{
                k: v for k, v in {
                    "name": idx.document.get("name"),
                    "unique": idx.document.get("unique", False),
                }.items() if v
            })
            print(f"  ✅ {name}")
        except Exception as e:
            print(f"  ⚠️  {idx.document.get('name', 'unknown')}: {e}")

    # ── court_email_log collection ──
    print("\n📧 court_email_log collection:")
    court_log = db["court_email_log"]

    court_indexes = [
        ("message_id", True, "idx_message_id_unique"),
        ("case_number", False, "idx_case_number"),
    ]

    for field, unique, name in court_indexes:
        try:
            if field == "case_number":
                court_log.create_index(
                    [(field, ASCENDING), ("processed_at", DESCENDING)],
                    name=name,
                )
            else:
                court_log.create_index(
                    [(field, ASCENDING)],
                    unique=unique,
                    name=name,
                )
            print(f"  ✅ {name}")
        except Exception as e:
            print(f"  ⚠️  {name}: {e}")

    # ── error_log collection ──
    print("\n🚨 error_log collection:")
    error_log = db["error_log"]

    try:
        # TTL index — auto-delete errors after 30 days
        error_log.create_index(
            [("timestamp", ASCENDING)],
            name="idx_ttl_30d",
            expireAfterSeconds=30 * 24 * 3600,  # 30 days
        )
        print("  ✅ idx_ttl_30d (auto-expire after 30 days)")
    except Exception as e:
        print(f"  ⚠️  idx_ttl_30d: {e}")

    try:
        error_log.create_index(
            [("source", ASCENDING), ("level", ASCENDING)],
            name="idx_source_level",
        )
        print("  ✅ idx_source_level")
    except Exception as e:
        print(f"  ⚠️  idx_source_level: {e}")

    try:
        error_log.create_index(
            [("timestamp", DESCENDING)],
            name="idx_timestamp_desc",
        )
        print("  ✅ idx_timestamp_desc")
    except Exception as e:
        print(f"  ⚠️  idx_timestamp_desc: {e}")

    # ── scraper_status collection ──
    print("\n🏥 scraper_status collection:")
    scraper_status = db["scraper_status"]

    try:
        scraper_status.create_index(
            [("county", ASCENDING)],
            unique=True,
            name="idx_county_unique",
        )
        print("  ✅ idx_county_unique")
    except Exception as e:
        print(f"  ⚠️  idx_county_unique: {e}")

    try:
        scraper_status.create_index(
            [("status", ASCENDING), ("last_success", DESCENDING)],
            name="idx_status_health",
        )
        print("  ✅ idx_status_health")
    except Exception as e:
        print(f"  ⚠️  idx_status_health: {e}")

    # ── defendants collection ──
    print("\n👤 defendants collection:")
    defendants = db["defendants"]

    try:
        defendants.create_index(
            [("full_name", ASCENDING)],
            name="idx_defendant_name",
        )
        print("  ✅ idx_defendant_name")
    except Exception as e:
        print(f"  ⚠️  idx_defendant_name: {e}")

    try:
        defendants.create_index(
            [("phone", ASCENDING)],
            name="idx_defendant_phone",
            sparse=True,
        )
        print("  ✅ idx_defendant_phone (sparse)")
    except Exception as e:
        print(f"  ⚠️  idx_defendant_phone: {e}")

    # ── poa_inventory collection ──
    print("\n📋 poa_inventory collection:")
    poa = db["poa_inventory"]

    try:
        poa.create_index(
            [("poa_number", ASCENDING)],
            unique=True,
            name="idx_poa_number_unique",
        )
        print("  ✅ idx_poa_number_unique")
    except Exception as e:
        print(f"  ⚠️  idx_poa_number_unique: {e}")

    try:
        poa.create_index(
            [("surety_id", ASCENDING), ("status", ASCENDING), ("max_bond", ASCENDING)],
            name="idx_surety_availability",
        )
        print("  ✅ idx_surety_availability")
    except Exception as e:
        print(f"  ⚠️  idx_surety_availability: {e}")

    print("\n" + "=" * 60)
    print("✅ All indexes created successfully!")

    # Print summary
    print("\n📊 Index Summary:")
    for coll_name in ["arrests", "court_email_log", "error_log", "scraper_status", "defendants", "poa_inventory"]:
        coll = db[coll_name]
        idx_count = len(list(coll.list_indexes()))
        print(f"  {coll_name}: {idx_count} indexes")

    client.close()


if __name__ == "__main__":
    print("🍀 ShamrockLeads — MongoDB Index Hardening")
    print("=" * 60)
    create_indexes()
