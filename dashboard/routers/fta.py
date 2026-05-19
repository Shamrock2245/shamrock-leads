# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
FTA (Failure to Appear) Alert API — ShamrockLeads
===================================================
Exposes the FTAAlertService to the dashboard frontend.

Endpoints:
  GET  /api/fta/open            — List open FTA alerts (with KPI stats)
  POST /api/fta/scan            — Trigger an immediate FTA scan
  POST /api/fta/<booking>/resolve — Resolve an FTA alert
  GET  /api/fta/stats           — Aggregate stats (for command center widget)
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection

log = logging.getLogger("shamrock.fta_api")

fta_bp = APIRouter(prefix="/api", tags=["fta"])
# ─────────────────────────────────────────────────────────────────────────────
# GET /api/fta/open
# ─────────────────────────────────────────────────────────────────────────────
@fta_bp.get("/fta/open")
async def api_fta_open(request: Request):
    """Return all open FTA alerts with KPI stats."""
    _qp = dict(request.query_params)
    try:
        level_filter = _qp.get("level", "")
        limit = min(int(_qp.get("limit", 100)), 200)

        fta_col = get_collection("fta_alerts")
        query = {"resolved": False}
        if level_filter and level_filter.isdigit():
            query["escalation_level"] = int(level_filter)

        cursor = (
            fta_col
            .find(query, {"_id": 0})
            .sort([("escalation_level", -1), ("detected_at", -1)])
            .limit(limit)
        )
        ftas = await cursor.to_list(length=limit)

        # KPI stats
        level3_count = sum(1 for f in ftas if f.get("escalation_level", 1) >= 3)
        exposure = sum(f.get("bond_amount", 0) for f in ftas)

        # Resolved in last 30 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resolved_30d = await fta_col.count_documents({
            "resolved": True,
            "resolved_at": {"$gte": cutoff},
        })

        return {
            "success": True,
            "ftas": ftas,
            "stats": {
                "open": len(ftas),
                "level3": level3_count,
                "resolved_30d": resolved_30d,
                "exposure_at_risk": exposure,
            },
        }
    except Exception as e:
        log.error("[FTA API] open error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/fta/scan
# ─────────────────────────────────────────────────────────────────────────────
@fta_bp.post("/fta/scan")
async def api_fta_scan():
    """Trigger an immediate FTA scan."""
    try:
        from dashboard.extensions import get_db
        from dashboard.services.fta_alert_service import FTAAlertService

        db = get_db()
        svc = FTAAlertService(db)
        result = await svc.scan_and_alert()

        return {"success": True, **result}
    except Exception as e:
        log.error("[FTA API] scan error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/fta/<booking_number>/resolve
# ─────────────────────────────────────────────────────────────────────────────
@fta_bp.post("/fta/<booking_number>/resolve")
async def api_fta_resolve(request: Request, booking_number: str):
    """Resolve an FTA alert."""
    try:
        data = await request.json() or {}
        resolution = data.get("resolution", "other")
        agent = data.get("agent", "staff")

        valid_resolutions = {
            "appeared", "warrant_recalled", "surrendered",
            "bond_reinstated", "other",
        }
        if resolution not in valid_resolutions:
            return JSONResponse({"success": False, "error": "Invalid resolution value"}, status_code=400)

        from dashboard.extensions import get_db
        from dashboard.services.fta_alert_service import FTAAlertService

        db = get_db()
        svc = FTAAlertService(db)
        result = await svc.resolve_fta(booking_number, resolution, agent)

        return result
    except Exception as e:
        log.error("[FTA API] resolve error for %s: %s", booking_number, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ───────────────────────────────────────────────────────────────────────────────
# POST /api/fta/<booking_number>/surrender
# ───────────────────────────────────────────────────────────────────────────────
@fta_bp.post("/fta/<booking_number>/surrender")
async def api_fta_initiate_surrender(request: Request, booking_number: str):
    """Initiate the Level 3 FTA surrender workflow."""
    try:
        data = await request.json() or {}
        initiated_by = data.get("initiated_by", "staff")
        notes = data.get("notes", "")

        from dashboard.services.fta_surrender_service import FTASurrenderService
        from dashboard.extensions import get_db

        db = get_db()
        svc = FTASurrenderService(db)
        result = await svc.initiate_surrender(
            booking_number=booking_number,
            initiated_by=initiated_by,
            notes=notes,
        )
        status = 200 if result.get("success") else 400
        return result, status
    except Exception as e:
        log.error("[FTA API] surrender error for %s: %s", booking_number, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ───────────────────────────────────────────────────────────────────────────────
# GET /api/fta/stats
# ───────────────────────────────────────────────────────────────────────────────
@fta_bp.get("/fta/stats")
async def api_fta_stats():
    """Aggregate FTA stats for the command center widget."""
    try:
        fta_col = get_collection("fta_alerts")

        open_count = await fta_col.count_documents({"resolved": False})
        level3_count = await fta_col.count_documents({
            "resolved": False,
            "escalation_level": {"$gte": 3},
        })
        surrender_count = await fta_col.count_documents({
            "resolved": False,
            "surrender_flagged": True,
        })

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resolved_30d = await fta_col.count_documents({
            "resolved": True,
            "resolved_at": {"$gte": cutoff},
        })

        # Exposure: sum bond_amount for all open FTAs
        pipeline = [
            {"$match": {"resolved": False}},
            {"$group": {"_id": None, "total": {"$sum": "$bond_amount"}}},
        ]
        agg = await fta_col.aggregate(pipeline).to_list(length=1)
        exposure = agg[0]["total"] if agg else 0

        return {
            "success": True,
            "open": open_count,
            "level3": level3_count,
            "surrender_flagged": surrender_count,
            "resolved_30d": resolved_30d,
            "exposure_at_risk": exposure,
        }
    except Exception as e:
        log.error("[FTA API] stats error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
