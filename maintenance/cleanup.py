"""
ShamrockLeads — Automated Data Cleanup & Purge

Runs on schedule to keep MongoDB lean under the Atlas M0 512MB cap.

Policy (configurable via env vars):
- Released inmates older than RETENTION_RELEASED_DAYS (default: 14) → purged
- Disqualified / cold stale rows by age tiers → purged
- Court-docket / Pending rows (CT Statewide) older than RETENTION_DOCKET_DAYS → purged
- Stale "In Custody" rows not re-scraped (updated_at old) + low value → purged
- Ingestion logs older than RETENTION_LOGS_DAYS (default: 7) → purged
- Emergency / hard-cap: delete **oldest** non-protected arrests by updated_at
  until under storage threshold or MAX_ARREST_DOCS

Protected forever (never auto-purged):
- booking_numbers on active/pending bonds
- bonded=True flags
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Set

from pymongo import MongoClient, ASCENDING
from config.settings import settings

logger = logging.getLogger(__name__)


# ── Retention Policy (from env or defaults) ──────────────────────────────────
RETENTION_RELEASED_DAYS = int(os.getenv("RETENTION_RELEASED_DAYS", "14"))
RETENTION_LOGS_DAYS = int(os.getenv("RETENTION_LOGS_DAYS", "7"))
RETENTION_DISQUALIFIED_DAYS = int(os.getenv("RETENTION_DISQUALIFIED_DAYS", "7"))
RETENTION_COLD_DAYS = int(os.getenv("RETENTION_COLD_DAYS", "30"))
RETENTION_DOCKET_DAYS = int(os.getenv("RETENTION_DOCKET_DAYS", "14"))
RETENTION_STALE_CUSTODY_DAYS = int(os.getenv("RETENTION_STALE_CUSTODY_DAYS", "21"))
# Soft cap on arrests collection size (docs). Oldest non-protected go first.
MAX_ARREST_DOCS = int(os.getenv("MAX_ARREST_DOCS", "80000"))
M0_LIMIT_MB = 512
EMERGENCY_THRESHOLD = float(os.getenv("RETENTION_EMERGENCY_PCT", "0.80"))  # 80%
HARD_CAP_THRESHOLD = float(os.getenv("RETENTION_HARD_CAP_PCT", "0.90"))    # 90%


def _protected_bookings(db) -> Set[str]:
    protected: Set[str] = set()
    try:
        for doc in db["active_bonds"].find(
            {"status": {"$in": ["active", "pending", "forfeited", "monitoring", "alert", "reinstated"]}},
            {"booking_number": 1, "Booking_Number": 1},
        ):
            bn = doc.get("booking_number") or doc.get("Booking_Number") or ""
            if bn:
                protected.add(str(bn))
    except Exception as e:
        logger.warning("Could not load active_bonds for protection: %s", e)
    try:
        for doc in db["intake_queue"].find(
            {"status": {"$nin": ["closed", "rejected", "disqualified"]}},
            {"matched_booking_number": 1, "booking_number": 1},
        ):
            for key in ("matched_booking_number", "booking_number"):
                bn = doc.get(key) or ""
                if bn:
                    protected.add(str(bn))
    except Exception as e:
        logger.warning("Could not load intake_queue for protection: %s", e)
    return protected


def _delete_oldest(
    coll,
    match: dict,
    limit: int,
    protected: Set[str],
    sort_field: str = "updated_at",
    protect_hot: bool = True,
) -> int:
    """Delete up to ``limit`` oldest matching docs, skipping protected bookings."""
    if limit <= 0:
        return 0
    cursor = coll.find(
        match,
        {"_id": 1, "booking_number": 1, "bonded": 1, "lead_status": 1},
    ).sort(sort_field, ASCENDING).limit(max(limit * 4, 100))  # oversample for skips
    ids = []
    for doc in cursor:
        bn = str(doc.get("booking_number") or "")
        if bn and bn in protected:
            continue
        if doc.get("bonded") is True:
            continue
        if protect_hot and (doc.get("lead_status") or "") == "Hot":
            continue
        ids.append(doc["_id"])
        if len(ids) >= limit:
            break
    if not ids:
        return 0
    return coll.delete_many({"_id": {"$in": ids}}).deleted_count


def run_cleanup() -> Dict[str, Any]:
    """
    Execute all cleanup tasks. Returns summary dict.
    Called by the scheduler on a recurring basis.
    """
    if not settings.mongo_configured():
        logger.warning("MongoDB not configured — skipping cleanup")
        return {"status": "skipped", "reason": "no_mongo"}

    client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=15000)
    db = client[settings.MONGODB_DB_NAME]
    arrests = db["arrests"]
    now = datetime.now(timezone.utc)
    results: Dict[str, Any] = {}
    protected = _protected_bookings(db)
    results["protected_bookings"] = len(protected)
    prot_list = list(protected) if protected else ["__none__"]

    try:
        # ── 1. Purge old Released / transferred inmates ─────────────────────
        released_cutoff = now - timedelta(days=RETENTION_RELEASED_DAYS)
        released_result = arrests.delete_many({
            "status": {"$regex": "released|bonded|discharged|transferred|exonerated", "$options": "i"},
            "updated_at": {"$lt": released_cutoff},
            "booking_number": {"$nin": prot_list},
            "bonded": {"$ne": True},
        })
        results["released_purged"] = released_result.deleted_count
        if released_result.deleted_count:
            logger.info(
                "🧹 Purged %d released inmates older than %dd",
                released_result.deleted_count, RETENTION_RELEASED_DAYS,
            )

        # ── 2. Disqualified / low-score cold (short window) ──────────────────
        disq_cutoff = now - timedelta(days=RETENTION_DISQUALIFIED_DAYS)
        disq_result = arrests.delete_many({
            "updated_at": {"$lt": disq_cutoff},
            "booking_number": {"$nin": prot_list},
            "bonded": {"$ne": True},
            "lead_status": {"$in": ["Disqualified", "Cold"]},
            "$or": [
                {"lead_score": {"$lte": 30}},
                {"bond_amount": {"$in": [0, 0.0, None, "0", ""]}},
            ],
        })
        results["disqualified_cold_purged"] = disq_result.deleted_count

        # ── 3. Cold leads past retention window (any status) ─────────────────
        cold_cutoff = now - timedelta(days=RETENTION_COLD_DAYS)
        cold_result = arrests.delete_many({
            "lead_status": "Cold",
            "updated_at": {"$lt": cold_cutoff},
            "booking_number": {"$nin": prot_list},
            "bonded": {"$ne": True},
        })
        results["cold_purged"] = cold_result.deleted_count

        # ── 4. Court docket / Pending (CT Statewide) — short TTL ─────────────
        docket_cutoff = now - timedelta(days=RETENTION_DOCKET_DAYS)
        docket_result = arrests.delete_many({
            "status": {"$regex": "^pending$", "$options": "i"},
            "updated_at": {"$lt": docket_cutoff},
            "booking_number": {"$nin": prot_list},
            "bonded": {"$ne": True},
        })
        results["docket_pending_purged"] = docket_result.deleted_count

        # ── 5. Stale in-custody not refreshed (left jail but still marked in) ─
        # If a roster scraper keeps running, live inmates get fresh updated_at.
        # Rows that stop appearing go stale → safe to drop after N days if low value.
        stale_cutoff = now - timedelta(days=RETENTION_STALE_CUSTODY_DAYS)
        stale_result = arrests.delete_many({
            "status": {"$regex": "custody|confined|held|sentenced|unsentenced", "$options": "i"},
            "updated_at": {"$lt": stale_cutoff},
            "booking_number": {"$nin": prot_list},
            "bonded": {"$ne": True},
            "$and": [
                {"$or": [
                    {"lead_status": {"$in": ["Cold", "Disqualified", None, ""]}},
                    {"lead_status": {"$exists": False}},
                ]},
                {"$or": [
                    {"lead_score": {"$lt": 50}},
                    {"bond_amount": {"$lte": 0}},
                    {"bond_amount": {"$exists": False}},
                ]},
            ],
        })
        results["stale_custody_purged"] = stale_result.deleted_count
        if stale_result.deleted_count:
            logger.info(
                "🧹 Purged %d stale in-custody rows (not re-scraped in %dd)",
                stale_result.deleted_count, RETENTION_STALE_CUSTODY_DAYS,
            )

        # ── 6. Ingestion logs ────────────────────────────────────────────────
        logs_cutoff = now - timedelta(days=RETENTION_LOGS_DAYS)
        logs_result = db["ingestion_log"].delete_many({"timestamp": {"$lt": logs_cutoff}})
        results["logs_purged"] = logs_result.deleted_count

        # ── 7. Orphan leads ──────────────────────────────────────────────────
        if db["leads"].estimated_document_count() > 0:
            arrest_ids = {str(doc["_id"]) for doc in arrests.find({}, {"_id": 1})}
            orphan_ids = [
                doc["_id"]
                for doc in db["leads"].find({}, {"arrest_id": 1})
                if str(doc.get("arrest_id", "")) not in arrest_ids
            ]
            if orphan_ids:
                orphan_result = db["leads"].delete_many({"_id": {"$in": orphan_ids}})
                results["orphan_leads_purged"] = orphan_result.deleted_count
            else:
                results["orphan_leads_purged"] = 0

        # ── 8. Document-count hard cap (oldest non-protected first) ──────────
        total_arrests = arrests.estimated_document_count()
        results["total_arrests_before_cap"] = total_arrests
        if total_arrests > MAX_ARREST_DOCS:
            over = total_arrests - MAX_ARREST_DOCS
            # Prefer low-value rows first, then any oldest
            n1 = _delete_oldest(
                arrests,
                {
                    "lead_status": {"$in": ["Cold", "Disqualified"]},
                    "booking_number": {"$nin": prot_list},
                    "bonded": {"$ne": True},
                },
                over,
                protected,
            )
            over -= n1
            n2 = 0
            if over > 0:
                n2 = _delete_oldest(
                    arrests,
                    {"booking_number": {"$nin": prot_list}, "bonded": {"$ne": True}},
                    over,
                    protected,
                )
            results["cap_evicted"] = n1 + n2
            logger.warning(
                "🧹 Arrest doc cap: evicted %d oldest (limit %d)",
                n1 + n2, MAX_ARREST_DOCS,
            )

        # ── 9. Storage size report + emergency ───────────────────────────────
        db_stats = db.command("dbStats")
        data_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_size_mb = round(db_stats.get("storageSize", 0) / (1024 * 1024), 2)
        index_size_mb = round(db_stats.get("indexSize", 0) / (1024 * 1024), 2)
        total_mb = round(storage_size_mb + index_size_mb, 2)
        results["db_data_size_mb"] = data_size_mb
        results["db_storage_size_mb"] = storage_size_mb
        results["db_index_size_mb"] = index_size_mb
        results["db_total_mb"] = total_mb
        results["total_arrests"] = arrests.estimated_document_count()
        results["total_logs"] = db["ingestion_log"].estimated_document_count()

        logger.info(
            "📊 DB Status: %.1fMB data / %.1fMB storage+index (%.0f%% of %dMB) | %s arrests",
            data_size_mb, total_mb, (total_mb / M0_LIMIT_MB) * 100, M0_LIMIT_MB,
            results["total_arrests"],
        )

        usage = total_mb / M0_LIMIT_MB if M0_LIMIT_MB else 0
        if usage > EMERGENCY_THRESHOLD:
            logger.warning(
                "⚠️ STORAGE PRESSURE: %.1fMB (%.0f%% of M0). Evicting oldest low-value rows…",
                total_mb, usage * 100,
            )
            # Batch-evict oldest cold/disqualified until under threshold or batch empty
            batch = 2000
            emergency_total = 0
            for _ in range(10):
                n = _delete_oldest(
                    arrests,
                    {
                        "lead_status": {"$in": ["Cold", "Disqualified"]},
                        "booking_number": {"$nin": prot_list},
                        "bonded": {"$ne": True},
                    },
                    batch,
                    protected,
                )
                emergency_total += n
                if n == 0:
                    break
                # re-check size
                st = db.command("dbStats")
                total_mb = (st.get("storageSize", 0) + st.get("indexSize", 0)) / (1024 * 1024)
                if total_mb / M0_LIMIT_MB <= EMERGENCY_THRESHOLD:
                    break
            # Still high → drop oldest released + pending dockets regardless of age
            if total_mb / M0_LIMIT_MB > EMERGENCY_THRESHOLD:
                r = arrests.delete_many({
                    "status": {"$regex": "released|bonded|discharged|pending", "$options": "i"},
                    "booking_number": {"$nin": prot_list},
                    "bonded": {"$ne": True},
                })
                emergency_total += r.deleted_count
            results["emergency_purged"] = emergency_total
            logger.warning("🚨 Emergency purged %d records", emergency_total)

            # Trim logs to 3 days
            emergency_log_cutoff = now - timedelta(days=3)
            db["ingestion_log"].delete_many({"timestamp": {"$lt": emergency_log_cutoff}})

        if usage > HARD_CAP_THRESHOLD:
            # Last resort: oldest anything non-protected
            n = _delete_oldest(
                arrests,
                {"booking_number": {"$nin": prot_list}, "bonded": {"$ne": True}},
                5000,
                protected,
            )
            results["hard_cap_evicted"] = n
            logger.error("🚨 HARD CAP: force-evicted %d oldest arrests", n)

        results["status"] = "success"
        results["cleaned_at"] = now.isoformat()

    except Exception as e:
        logger.error("Cleanup failed: %s", e)
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
