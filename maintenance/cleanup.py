"""
ShamrockLeads — Automated Data Cleanup & Purge

Runs on schedule to keep MongoDB lean and disk space free.
Respects the 512MB M0 Atlas limit by purging stale data.

Policy (configurable via env vars):
- Released inmates older than RETENTION_RELEASED_DAYS (default: 30) → purged
- Ingestion logs older than RETENTION_LOGS_DAYS (default: 14) → purged
- Disqualified leads (score=0, status=Released) older than RETENTION_DISQUALIFIED_DAYS (default: 7) → purged
- Active/In-Custody records are NEVER auto-purged
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from pymongo import MongoClient
from config.settings import settings

logger = logging.getLogger(__name__)


# ── Retention Policy (from env or defaults) ──────────────────────────────────
RETENTION_RELEASED_DAYS = int(os.getenv("RETENTION_RELEASED_DAYS", "30"))
RETENTION_LOGS_DAYS = int(os.getenv("RETENTION_LOGS_DAYS", "14"))
RETENTION_DISQUALIFIED_DAYS = int(os.getenv("RETENTION_DISQUALIFIED_DAYS", "7"))


def run_cleanup() -> Dict[str, Any]:
    """
    Execute all cleanup tasks. Returns summary dict.
    Called by the scheduler on a recurring basis.
    """
    if not settings.mongo_configured():
        logger.warning("MongoDB not configured — skipping cleanup")
        return {"status": "skipped", "reason": "no_mongo"}

    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    now = datetime.now(timezone.utc)
    results = {}

    try:
        # ── 1. Purge old Released inmates ────────────────────────────────────
        # These are people who have been released from jail.
        # They are no longer actionable leads.
        released_cutoff = now - timedelta(days=RETENTION_RELEASED_DAYS)
        released_result = db["arrests"].delete_many({
            "status": {"$regex": "released|bonded|discharged|transferred", "$options": "i"},
            "updated_at": {"$lt": released_cutoff},
        })
        results["released_purged"] = released_result.deleted_count
        if released_result.deleted_count > 0:
            logger.info(
                f"🧹 Purged {released_result.deleted_count} released inmates "
                f"older than {RETENTION_RELEASED_DAYS} days"
            )

        # ── 2. Purge disqualified leads (score 0, $0 bond) ──────────────────
        disqualified_cutoff = now - timedelta(days=RETENTION_DISQUALIFIED_DAYS)
        disqualified_result = db["arrests"].delete_many({
            "$or": [
                {"lead_status": "Disqualified"},
                {"lead_score": 0, "bond_amount": 0},
                {"bond_amount": {"$in": [0, None, "0"]}},
            ],
            "status": {"$regex": "released|bonded|discharged", "$options": "i"},
            "updated_at": {"$lt": disqualified_cutoff},
        })
        results["disqualified_purged"] = disqualified_result.deleted_count
        if disqualified_result.deleted_count > 0:
            logger.info(
                f"🧹 Purged {disqualified_result.deleted_count} disqualified leads "
                f"older than {RETENTION_DISQUALIFIED_DAYS} days"
            )

        # ── 3. Purge old ingestion logs ──────────────────────────────────────
        # These are run-level stats — useful for debugging but not forever.
        logs_cutoff = now - timedelta(days=RETENTION_LOGS_DAYS)
        logs_result = db["ingestion_log"].delete_many({
            "timestamp": {"$lt": logs_cutoff},
        })
        results["logs_purged"] = logs_result.deleted_count
        if logs_result.deleted_count > 0:
            logger.info(
                f"🧹 Purged {logs_result.deleted_count} ingestion logs "
                f"older than {RETENTION_LOGS_DAYS} days"
            )

        # ── 4. Purge orphaned leads collection entries ───────────────────────
        # The 'leads' collection may have entries whose arrest_id no longer
        # exists in 'arrests' (because the arrest was purged above).
        if db["leads"].estimated_document_count() > 0:
            arrest_ids = set(
                str(doc["_id"])
                for doc in db["arrests"].find({}, {"_id": 1})
            )
            orphan_cursor = db["leads"].find({}, {"arrest_id": 1})
            orphan_ids = [
                doc["_id"]
                for doc in orphan_cursor
                if str(doc.get("arrest_id", "")) not in arrest_ids
            ]
            if orphan_ids:
                orphan_result = db["leads"].delete_many({"_id": {"$in": orphan_ids}})
                results["orphan_leads_purged"] = orphan_result.deleted_count
                logger.info(f"🧹 Purged {orphan_result.deleted_count} orphaned leads")
            else:
                results["orphan_leads_purged"] = 0

        # ── 5. Report current DB size ────────────────────────────────────────
        db_stats = db.command("dbStats")
        data_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_size_mb = round(db_stats.get("storageSize", 0) / (1024 * 1024), 2)
        results["db_data_size_mb"] = data_size_mb
        results["db_storage_size_mb"] = storage_size_mb
        results["total_arrests"] = db["arrests"].estimated_document_count()
        results["total_logs"] = db["ingestion_log"].estimated_document_count()

        logger.info(
            f"📊 DB Status: {data_size_mb}MB data / {storage_size_mb}MB storage | "
            f"{results['total_arrests']} arrests | {results['total_logs']} logs"
        )

        # ── 6. Emergency cleanup if approaching M0 limit ─────────────────────
        M0_LIMIT_MB = 512
        EMERGENCY_THRESHOLD = 0.85  # 85% = ~435MB
        if storage_size_mb > (M0_LIMIT_MB * EMERGENCY_THRESHOLD):
            logger.warning(
                f"⚠️ EMERGENCY: DB at {storage_size_mb}MB "
                f"({round(storage_size_mb / M0_LIMIT_MB * 100, 1)}% of {M0_LIMIT_MB}MB limit)"
            )
            # Aggressive purge: remove ALL released records regardless of age
            emergency_result = db["arrests"].delete_many({
                "status": {"$regex": "released|bonded|discharged|transferred", "$options": "i"},
            })
            results["emergency_purged"] = emergency_result.deleted_count
            logger.warning(f"🚨 Emergency purged {emergency_result.deleted_count} released records")

            # Also trim ingestion logs to last 3 days
            emergency_log_cutoff = now - timedelta(days=3)
            db["ingestion_log"].delete_many({"timestamp": {"$lt": emergency_log_cutoff}})

        results["status"] = "success"

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        results["status"] = "error"
        results["error"] = str(e)

    finally:
        client.close()

    return results


if __name__ == "__main__":
    """Allow running cleanup manually: python -m maintenance.cleanup"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = run_cleanup()
    print(f"\nCleanup Results: {result}")
