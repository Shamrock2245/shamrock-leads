# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — Data Retention Service
Auto-purge old, low-value arrest records to keep MongoDB under the 512MB M0 ceiling.

Policy:
  - Arrests older than 90 days with lead_score < 30 → purge
  - Arrests older than 180 days with lead_score < 50 and not linked to a bond → purge
  - Arrests older than 365 days with status "Released" and no bond → purge
  - Dismissed notifications older than 30 days → purge
  - Read notifications older than 90 days → purge
  - Completed outreach sequences older than 60 days → archive metadata only

Endpoints:
  GET  /retention/status     — Current DB size and purge estimates
  POST /retention/purge      — Execute purge (with dry_run option)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta

from dashboard.extensions import get_collection, get_db

retention_bp = APIRouter(prefix="/api", tags=["retention"])
async def _estimate_purge() -> dict:
    """Estimate how many records would be purged under current policy."""
    arrests_col = get_collection("arrests")
    notif_col = get_collection("notifications")
    now = datetime.now(timezone.utc)

    estimates = {}

    # 90-day cold leads
    cutoff_90 = (now - timedelta(days=90)).isoformat()
    estimates["arrests_90d_cold"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_90},
        "lead_score": {"$lt": 30},
    })

    # 180-day warm leads without bonds
    cutoff_180 = (now - timedelta(days=180)).isoformat()
    estimates["arrests_180d_warm_unbonded"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_180},
        "lead_score": {"$lt": 50},
        "bonded": {"$ne": True},
    })

    # 365-day released
    cutoff_365 = (now - timedelta(days=365)).isoformat()
    estimates["arrests_365d_released"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_365},
        "$or": [
            {"custody_status": {"$regex": "released", "$options": "i"}},
            {"custody_status": {"$regex": "discharged", "$options": "i"}},
        ],
        "bonded": {"$ne": True},
    })

    # Old notifications
    cutoff_notif_30 = (now - timedelta(days=30)).isoformat()
    estimates["notifications_dismissed_30d"] = await notif_col.count_documents({
        "dismissed": True,
        "created_at": {"$lt": cutoff_notif_30},
    })

    cutoff_notif_90 = (now - timedelta(days=90)).isoformat()
    estimates["notifications_read_90d"] = await notif_col.count_documents({
        "read": True,
        "created_at": {"$lt": cutoff_notif_90},
    })

    estimates["total_purgeable"] = sum(estimates.values())
    return estimates


async def _execute_purge(dry_run: bool = True) -> dict:
    """Execute the purge policy. Returns counts of deleted records."""
    arrests_col = get_collection("arrests")
    notif_col = get_collection("notifications")
    now = datetime.now(timezone.utc)
    results = {}

    if dry_run:
        return await _estimate_purge()

    # 90-day cold leads
    cutoff_90 = (now - timedelta(days=90)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_90},
        "lead_score": {"$lt": 30},
    })
    results["arrests_90d_cold"] = r.deleted_count

    # 180-day warm unbonded
    cutoff_180 = (now - timedelta(days=180)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_180},
        "lead_score": {"$lt": 50},
        "bonded": {"$ne": True},
    })
    results["arrests_180d_warm_unbonded"] = r.deleted_count

    # 365-day released unbonded
    cutoff_365 = (now - timedelta(days=365)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_365},
        "$or": [
            {"custody_status": {"$regex": "released", "$options": "i"}},
            {"custody_status": {"$regex": "discharged", "$options": "i"}},
        ],
        "bonded": {"$ne": True},
    })
    results["arrests_365d_released"] = r.deleted_count

    # Old dismissed notifications
    cutoff_notif_30 = (now - timedelta(days=30)).isoformat()
    r = await notif_col.delete_many({
        "dismissed": True,
        "created_at": {"$lt": cutoff_notif_30},
    })
    results["notifications_dismissed_30d"] = r.deleted_count

    # Old read notifications
    cutoff_notif_90 = (now - timedelta(days=90)).isoformat()
    r = await notif_col.delete_many({
        "read": True,
        "created_at": {"$lt": cutoff_notif_90},
    })
    results["notifications_read_90d"] = r.deleted_count

    results["total_purged"] = sum(results.values())
    results["purged_at"] = now.isoformat()
    return results


@retention_bp.get("/retention/status")
async def retention_status():
    """Show current DB size and purge estimates."""
    db = get_db()

    # Get DB stats
    try:
        stats = await db.command("dbstats")
        db_size_mb = round(stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_mb = round(stats.get("storageSize", 0) / (1024 * 1024), 2)
        collections = stats.get("collections", 0)
        objects = stats.get("objects", 0)
    except Exception:
        db_size_mb = 0
        storage_mb = 0
        collections = 0
        objects = 0

    estimates = await _estimate_purge()

    return {
        "database": {
            "data_size_mb": db_size_mb,
            "storage_size_mb": storage_mb,
            "collections": collections,
            "total_documents": objects,
            "limit_mb": 512,
            "usage_pct": round((storage_mb / 512) * 100, 1) if storage_mb else 0,
        },
        "purge_estimates": estimates,
    }


@retention_bp.post("/retention/purge")
async def execute_purge():
    """Execute the purge. Pass dry_run=true to preview."""
    data = await request.json() or {}
    dry_run = data.get("dry_run", True)

    if isinstance(dry_run, str):
        dry_run = dry_run.lower() != "false"

    results = await _execute_purge(dry_run=dry_run)
    return {
        "mode": "dry_run" if dry_run else "executed",
        "results": results,
    }
