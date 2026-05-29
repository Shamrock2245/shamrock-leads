
"""
Docket Monitor API — ShamrockLeads Intelligence Suite
=====================================================
REST endpoints for the real-time docket monitoring engine.

Routes:
  GET  /api/docket-monitor/status        — Overall monitoring stats
  GET  /api/docket-monitor/events        — Recent docket events (filterable)
  GET  /api/docket-monitor/events/<id>   — Single event detail
  GET  /api/docket-monitor/bond/<bid>    — Events for a specific bond
  GET  /api/docket-monitor/alerts        — Unacknowledged alert summary
  POST /api/docket-monitor/scan          — Trigger manual scan
  POST /api/docket-monitor/acknowledge   — Acknowledge an event
  POST /api/docket-monitor/acknowledge-all — Bulk acknowledge
"""
import logging
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_db

log = logging.getLogger("shamrock.api.docket_monitor")
docket_monitor_bp = APIRouter(prefix="/api/docket-monitor", tags=["docket_monitor"])
def _get_monitor():
    """Lazy-init the DocketMonitor service."""
    import os
    from dashboard.services.docket_monitor import DocketMonitor
    db = get_db()
    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    return DocketMonitor(db, courtlistener_token=token)


@docket_monitor_bp.get("/status")
async def monitoring_status():
    """Overall monitoring statistics."""
    try:
        monitor = _get_monitor()
        stats = await monitor.get_monitoring_stats()
        return stats
    except Exception as e:
        log.error("Status error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.get("/events")
async def recent_events(limit: int = Query(default=50), severity: str | None = Query(default=None), acknowledged: str | None = Query(default=None)):
    """Recent docket events. Query params: limit, severity, acknowledged."""
    try:
        monitor = _get_monitor()
        limit = min(int(limit), 200)
        severity = severity
        ack_str = acknowledged
        ack = None
        if ack_str == "true":
            ack = True
        elif ack_str == "false":
            ack = False
        events = await monitor.get_recent_events(limit=limit, severity=severity, acknowledged=ack)
        return {"success": True, "events": events, "count": len(events)}
    except Exception as e:
        log.error("Events error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.get("/events/{event_id}")
async def event_detail(event_id):
    """Single event detail."""
    try:
        from bson import ObjectId
        db = get_db()
        event = await db.docket_events.find_one({"_id": ObjectId(event_id)})
        if not event:
            return JSONResponse({"success": False, "error": "Event not found"}, status_code=404)
        event["_id"] = str(event["_id"])
        return {"success": True, "event": event}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.get("/bond/{bond_case_id}")
async def bond_events(bond_case_id):
    """All docket events for a specific bond."""
    try:
        monitor = _get_monitor()
        events = await monitor.get_bond_events(bond_case_id)
        # Compute cumulative risk adjustment
        total_risk_adj = sum(e.get("risk_adjustment", 0) for e in events)
        return {
            "success": True, "events": events, "count": len(events),
            "cumulative_risk_adjustment": round(total_risk_adj, 3),
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.get("/alerts")
async def alert_summary():
    """Unacknowledged alerts grouped by severity."""
    try:
        monitor = _get_monitor()
        summary = await monitor.get_alert_summary()
        return {"success": True, **summary}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.post("/scan")
async def trigger_scan(request: Request):
    """Manually trigger a docket scan of active bonds."""
    try:
        body = await request.json() or {}
        limit = min(int(body.get("limit", 50)), 200)
        from dashboard.services.docket_monitor import run_docket_scan
        db = get_db()
        result = await run_docket_scan(db, limit=limit)
        return result
    except Exception as e:
        log.error("Scan error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.post("/acknowledge")
async def acknowledge_event(request: Request):
    """Acknowledge a single docket event by _id."""
    try:
        body = await request.json() or {}
        event_id = body.get("event_id")
        actor = body.get("actor", "dashboard_user")
        if not event_id:
            return JSONResponse({"success": False, "error": "event_id required"}, status_code=400)
        monitor = _get_monitor()
        ok = await monitor.acknowledge_event(event_id, actor=actor)
        return {"success": ok}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.post("/acknowledge-all")
async def acknowledge_all(request: Request):
    """Bulk acknowledge all unacknowledged events (optionally filtered by severity)."""
    try:
        body = await request.json() or {}
        severity = body.get("severity")
        actor = body.get("actor", "dashboard_user")
        db = get_db()
        query = {"acknowledged": False}
        if severity:
            query["event_severity"] = severity
        from datetime import datetime, timezone
        result = await db.docket_events.update_many(query, {"$set": {
            "acknowledged": True, "acknowledged_by": actor,
            "acknowledged_at": datetime.now(timezone.utc).isoformat() + "Z",
        }})
        return {"success": True, "acknowledged_count": result.modified_count}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@docket_monitor_bp.get("/risk-timeline/{bond_case_id}")
async def risk_timeline(bond_case_id):
    """Get risk adjustment timeline for a bond — shows how docket events shift risk over time."""
    try:
        db = get_db()
        cursor = db.docket_events.find(
            {"bond_case_id": bond_case_id},
            {"event_type": 1, "event_severity": 1, "risk_adjustment": 1,
             "event_date": 1, "detected_at": 1, "description": 1},
        ).sort("detected_at", 1)
        events = await cursor.to_list(length=200)
        timeline = []
        cumulative = 0.0
        for e in events:
            cumulative += e.get("risk_adjustment", 0)
            timeline.append({
                "date": e.get("event_date") or e.get("detected_at", ""),
                "event_type": e.get("event_type"),
                "severity": e.get("event_severity"),
                "adjustment": e.get("risk_adjustment", 0),
                "cumulative_risk": round(cumulative, 3),
                "description": e.get("description", ""),
            })
        return {"success": True, "bond_case_id": bond_case_id,
                        "timeline": timeline, "total_risk_shift": round(cumulative, 3)}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
