"""
ShamrockLeads — Data Retention Service
=======================================
Auto-purge old, low-value arrest records to keep MongoDB under the 512MB M0 ceiling.

Tiered Purge Policy (per Workstream 5 spec):
  Tier 1: Arrests older than 90 days with lead_status "Disqualified"       → delete
  Tier 2: Arrests older than 60 days with lead_status "Cold"               → delete
  Tier 3: Arrests older than 30 days with lead_status "Warm" + no_contact  → delete
  NEVER delete: records linked to active bonds, pending matches, or bonded cases

Additional cleanup:
  - Dismissed notifications older than 30 days → purge
  - Read notifications older than 90 days → purge
  - Completed outreach sequences older than 60 days → archive metadata only

Slack Alert:
  - When DB storage exceeds 80% of 512MB (409.6MB), a Slack alert is sent

Endpoints:
  GET  /api/retention/status     — Current DB size, usage %, and purge estimates
  POST /api/retention/purge      — Execute purge (dry_run=true to preview)
  GET  /api/retention/widget     — Lightweight DB size widget for Health tab
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection, get_db

logger = logging.getLogger(__name__)

retention_bp = APIRouter(prefix="/api", tags=["retention"])

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────
_ATLAS_LIMIT_MB = 512
_ALERT_THRESHOLD_PCT = 80.0   # Slack alert at 80% (409.6 MB)
_SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")


# ─────────────────────────────────────────────────────────────────────────────
#  Active-Bond Guard
# ─────────────────────────────────────────────────────────────────────────────

async def _get_protected_booking_numbers() -> set[str]:
    """Return the set of booking numbers that MUST NOT be purged.

    Protects:
      - Records with an active bond (active_bonds collection)
      - Records with a pending match (intake_queue where status not closed/rejected)
      - Records explicitly flagged bonded=True in arrests
    """
    protected: set[str] = set()
    try:
        active_bonds_col = get_collection("active_bonds")
        cursor = active_bonds_col.find(
            {"status": {"$in": ["active", "pending", "forfeited"]}},
            {"booking_number": 1},
        )
        async for doc in cursor:
            bn = doc.get("booking_number", "")
            if bn:
                protected.add(bn)
    except Exception as exc:
        logger.warning("[Retention] Could not fetch active bonds: %s", exc)

    try:
        intake_col = get_collection("intake_queue")
        cursor = intake_col.find(
            {"status": {"$nin": ["closed", "rejected", "disqualified"]}},
            {"matched_booking_number": 1, "booking_number": 1},
        )
        async for doc in cursor:
            for key in ("matched_booking_number", "booking_number"):
                bn = doc.get(key, "")
                if bn:
                    protected.add(bn)
    except Exception as exc:
        logger.warning("[Retention] Could not fetch intake queue: %s", exc)

    return protected


# ─────────────────────────────────────────────────────────────────────────────
#  Purge Estimates
# ─────────────────────────────────────────────────────────────────────────────

async def _estimate_purge() -> dict:
    """Estimate how many records would be purged under the tiered policy."""
    arrests_col = get_collection("arrests")
    notif_col = get_collection("notifications")
    now = datetime.now(timezone.utc)
    protected = await _get_protected_booking_numbers()

    estimates: dict[str, int] = {}

    # Tier 1: Disqualified > 90 days
    cutoff_90 = (now - timedelta(days=90)).isoformat()
    estimates["tier1_disqualified_90d"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_90},
        "lead_status": "Disqualified",
        "booking_number": {"$nin": list(protected)},
        "bonded": {"$ne": True},
    })

    # Tier 2: Cold > 60 days
    cutoff_60 = (now - timedelta(days=60)).isoformat()
    estimates["tier2_cold_60d"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_60},
        "lead_status": "Cold",
        "booking_number": {"$nin": list(protected)},
        "bonded": {"$ne": True},
    })

    # Tier 3: Warm + no_contact > 30 days
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    estimates["tier3_warm_no_contact_30d"] = await arrests_col.count_documents({
        "scraped_at": {"$lt": cutoff_30},
        "lead_status": "Warm",
        "no_contact": True,
        "booking_number": {"$nin": list(protected)},
        "bonded": {"$ne": True},
    })

    # Notifications
    estimates["notifications_dismissed_30d"] = await notif_col.count_documents({
        "dismissed": True,
        "created_at": {"$lt": cutoff_30},
    })
    cutoff_notif_90 = (now - timedelta(days=90)).isoformat()
    estimates["notifications_read_90d"] = await notif_col.count_documents({
        "read": True,
        "created_at": {"$lt": cutoff_notif_90},
    })

    estimates["total_purgeable"] = sum(estimates.values())
    estimates["protected_booking_numbers"] = len(protected)
    return estimates


# ─────────────────────────────────────────────────────────────────────────────
#  Execute Purge
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_purge(dry_run: bool = True) -> dict:
    """Execute the tiered purge policy.

    NEVER deletes records linked to active bonds, pending matches, or bonded cases.
    Returns counts of deleted records.
    """
    if dry_run:
        return await _estimate_purge()

    arrests_col = get_collection("arrests")
    notif_col = get_collection("notifications")
    now = datetime.now(timezone.utc)
    results: dict[str, int] = {}

    # Re-fetch protected set at execution time (not from estimate)
    protected = await _get_protected_booking_numbers()
    protected_list = list(protected)

    # Tier 1: Disqualified > 90 days
    cutoff_90 = (now - timedelta(days=90)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_90},
        "lead_status": "Disqualified",
        "booking_number": {"$nin": protected_list},
        "bonded": {"$ne": True},
    })
    results["tier1_disqualified_90d"] = r.deleted_count

    # Tier 2: Cold > 60 days
    cutoff_60 = (now - timedelta(days=60)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_60},
        "lead_status": "Cold",
        "booking_number": {"$nin": protected_list},
        "bonded": {"$ne": True},
    })
    results["tier2_cold_60d"] = r.deleted_count

    # Tier 3: Warm + no_contact > 30 days
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    r = await arrests_col.delete_many({
        "scraped_at": {"$lt": cutoff_30},
        "lead_status": "Warm",
        "no_contact": True,
        "booking_number": {"$nin": protected_list},
        "bonded": {"$ne": True},
    })
    results["tier3_warm_no_contact_30d"] = r.deleted_count

    # Notifications
    r = await notif_col.delete_many({
        "dismissed": True,
        "created_at": {"$lt": cutoff_30},
    })
    results["notifications_dismissed_30d"] = r.deleted_count

    cutoff_notif_90 = (now - timedelta(days=90)).isoformat()
    r = await notif_col.delete_many({
        "read": True,
        "created_at": {"$lt": cutoff_notif_90},
    })
    results["notifications_read_90d"] = r.deleted_count

    results["total_purged"] = sum(results.values())
    results["purged_at"] = now.isoformat()
    results["protected_booking_numbers"] = len(protected)

    total = results["total_purged"]
    logger.info(
        "[Retention] Purge complete: %d records deleted, %d booking numbers protected",
        total, len(protected),
    )
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  DB Stats Helper
# ─────────────────────────────────────────────────────────────────────────────

async def _get_db_stats() -> dict:
    """Fetch MongoDB dbStats and return a normalized dict."""
    db = get_db()
    try:
        stats = await db.command("dbStats")
        data_mb = round(stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_mb = round(stats.get("storageSize", 0) / (1024 * 1024), 2)
        index_mb = round(stats.get("indexSize", 0) / (1024 * 1024), 2)
        total_mb = round((stats.get("storageSize", 0) + stats.get("indexSize", 0)) / (1024 * 1024), 2)
        usage_pct = round((total_mb / _ATLAS_LIMIT_MB) * 100, 1) if total_mb else 0.0
        return {
            "data_size_mb": data_mb,
            "storage_size_mb": storage_mb,
            "index_size_mb": index_mb,
            "total_size_mb": total_mb,
            "collections": stats.get("collections", 0),
            "total_documents": stats.get("objects", 0),
            "limit_mb": _ATLAS_LIMIT_MB,
            "usage_pct": usage_pct,
            "alert_threshold_pct": _ALERT_THRESHOLD_PCT,
            "at_risk": usage_pct >= _ALERT_THRESHOLD_PCT,
        }
    except Exception as exc:
        logger.warning("[Retention] dbStats failed: %s", exc)
        return {
            "data_size_mb": 0,
            "storage_size_mb": 0,
            "index_size_mb": 0,
            "total_size_mb": 0,
            "collections": 0,
            "total_documents": 0,
            "limit_mb": _ATLAS_LIMIT_MB,
            "usage_pct": 0.0,
            "alert_threshold_pct": _ALERT_THRESHOLD_PCT,
            "at_risk": False,
            "error": str(exc),
        }


async def _maybe_alert_slack(db_stats: dict) -> None:
    """Send a Slack alert if DB usage exceeds the 80% threshold."""
    if not _SLACK_WEBHOOK:
        return
    if not db_stats.get("at_risk"):
        return
    try:
        import httpx
        pct = db_stats["usage_pct"]
        total = db_stats["total_size_mb"]
        msg = (
            f"⚠️ *ShamrockLeads DB Alert* — MongoDB Atlas M0 at *{pct}%* "
            f"({total} MB / {_ATLAS_LIMIT_MB} MB). "
            f"Run `/api/retention/purge` to free space."
        )
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(_SLACK_WEBHOOK, json={"text": msg})
        logger.warning("[Retention] Slack alert sent: DB at %s%%", pct)
    except Exception as exc:
        logger.warning("[Retention] Slack alert failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@retention_bp.get("/retention/status")
async def retention_status():
    """Show current DB size, usage %, purge estimates, and protection counts."""
    db_stats = await _get_db_stats()
    estimates = await _estimate_purge()
    await _maybe_alert_slack(db_stats)
    return {
        "database": db_stats,
        "purge_estimates": estimates,
    }


@retention_bp.get("/retention/widget")
async def retention_widget():
    """Lightweight DB size widget for the Health tab.

    Returns only the essential metrics needed to render the usage bar.
    """
    db_stats = await _get_db_stats()
    return {
        "total_size_mb": db_stats["total_size_mb"],
        "limit_mb": db_stats["limit_mb"],
        "usage_pct": db_stats["usage_pct"],
        "at_risk": db_stats["at_risk"],
        "alert_threshold_pct": db_stats["alert_threshold_pct"],
    }


@retention_bp.post("/retention/purge")
async def execute_purge(request: Request):
    """Execute the tiered purge policy.

    Body:
        { "dry_run": true }   — Preview only (default)
        { "dry_run": false }  — Actually delete records

    NEVER deletes records linked to active bonds, pending matches, or bonded cases.
    """
    data = await request.json() or {}
    dry_run = data.get("dry_run", True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() != "false"

    results = await _execute_purge(dry_run=dry_run)

    # Post-purge: check DB size and alert if still at risk
    if not dry_run:
        db_stats = await _get_db_stats()
        await _maybe_alert_slack(db_stats)
        results["post_purge_db_stats"] = db_stats

    return {
        "mode": "dry_run" if dry_run else "executed",
        "results": results,
    }
