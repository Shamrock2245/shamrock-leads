from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request
"""
ShamrockLeads — Automation & Service Control API
==================================================
Dashboard endpoints for controlling ALL background services.

GET  /api/automation/config           → Current config (all toggles)
POST /api/automation/config           → Update config (partial updates)
POST /api/automation/toggle/<key>     → Quick enable/disable a specific service
POST /api/automation/trigger/<key>    → Manually trigger one immediate cycle
GET  /api/automation/status           → Current runtime status of all services

All endpoints require authentication (existing session-based auth).
"""
import asyncio
import logging
from datetime import datetime, timezone

from dashboard.deps import get_db
from dashboard.services.automation_config import (
    get_automation_config,
    update_automation_config,
    is_enabled,
)

logger = logging.getLogger(__name__)

automation_control_bp = APIRouter(prefix="/api", tags=["automation"])
# ── Global trigger-event registry ─────────────────────────────────────────────
# Each cron loop in __init__.py registers an asyncio.Event here.
# POST /api/automation/trigger/<key> sets the event, causing the loop to skip
# its current sleep and run one cycle immediately.
# Keys are populated lazily by the cron loops themselves at startup.
TRIGGER_EVENTS: dict[str, asyncio.Event] = {}


def register_trigger(key: str, event: asyncio.Event) -> None:
    """Called by each cron loop in __init__.py to register its wake-up event."""
    TRIGGER_EVENTS[key] = event


# ── All controllable service keys ──────────────────────────────────────────
ALL_SERVICE_KEYS = {
    # Revenue Automation
    "speed_to_contact", "paperwork_chase", "intake_recovery", "auto_reply",
    "poa_low_stock", "surety_weekly_reports",
    # Lifecycle suite
    "forfeiture_scan", "signnow_poller", "compliance_backfill", "matching_backlog",
    # Intelligence Pipeline
    "docket_monitor", "court_intel", "nlp_enrichment",
    # Monitoring & Compliance
    "court_reminders", "rearrest_detection", "delinquency_scanner",
    "court_email", "bb_health", "data_retention",
    # Geo Intelligence
    "geo_intelligence", "findmy_geofence",
    # Content
    "blog_publisher", "wix_sync",
    # Utilitarian (Node-RED orchestrated)
    "daily_ops_digest", "scraper_health_webhook", "fta_alert_relay", "bond_status_sync",
    # Node-RED only flows (surfaced for visibility)
    "nr_social_autopilot", "nr_court_clerk", "nr_the_closer", "nr_morning_briefing",
    "nr_bounty_hunter", "nr_watchdog", "nr_gas_scheduler", "nr_intake_pipeline",
    "nr_revenue_snapshot", "nr_the_scout", "nr_staff_performance", "nr_weather_posting",
    "nr_review_harvester", "nr_payment_reminders", "nr_no_show_escalation",
    "nr_whatsapp_campaigns", "nr_signnow_tracker", "nr_bond_renewal",
}

# Service metadata for frontend rendering
SERVICE_META = {
    "speed_to_contact":    {"name": "Speed-to-Contact",    "icon": "🚀", "category": "revenue",  "desc": "Queue hot-lead outreach for approval (review mode)"},
    "paperwork_chase":     {"name": "Paperwork Chase",     "icon": "📋", "category": "revenue",  "desc": "Unsigned packet chase — review/staff/full_auto"},
    "intake_recovery":     {"name": "Intake Recovery",     "icon": "🔄", "category": "revenue",  "desc": "Recover abandoned intakes (review by default)"},
    "poa_low_stock":       {"name": "POA Low Stock",       "icon": "📕", "category": "revenue",  "desc": "Slack when POA inventory tiers run low"},
    "surety_weekly_reports": {"name": "Surety Weekly Reports", "icon": "📊", "category": "revenue", "desc": "Official OSI/Palmetto weekly XLSX into Mongo"},
    "forfeiture_scan":     {"name": "Forfeiture Scan",     "icon": "🔴", "category": "lifecycle", "desc": "Score active bonds; Slack high/critical risk"},
    "signnow_poller":      {"name": "SignNow Poller",      "icon": "✍️", "category": "lifecycle", "desc": "Sync packet status; collect-payment tasks"},
    "compliance_backfill": {"name": "Compliance Backfill", "icon": "✅", "category": "lifecycle", "desc": "Create missing check-in/court tasks"},
    "matching_backlog":    {"name": "Matching Backlog",    "icon": "🔗", "category": "lifecycle", "desc": "Batch-match intakes; human on ambiguity"},
    "auto_reply":          {"name": "AI Auto-Reply",       "icon": "🤖", "category": "revenue",  "desc": "AI responds to inbound iMessages"},
    "docket_monitor":      {"name": "Docket Monitor",      "icon": "⚖️", "category": "intel",    "desc": "CourtListener docket scan for active bonds"},
    "court_intel":         {"name": "Court Intelligence",  "icon": "🏛️", "category": "intel",    "desc": "Court opinion ingestion (30-day window)"},
    "nlp_enrichment":      {"name": "NLP Enrichment",      "icon": "🧠", "category": "intel",    "desc": "Charge analysis & FTA risk scoring"},
    "court_reminders":     {"name": "Court Reminders",     "icon": "📅", "category": "monitor",  "desc": "4-touch SMS court date reminders"},
    "rearrest_detection":  {"name": "Re-Arrest Detection", "icon": "🔁", "category": "monitor",  "desc": "Cross-reference new arrests vs active bonds"},
    "delinquency_scanner": {"name": "Delinquency Scanner", "icon": "💳", "category": "monitor",  "desc": "Flag overdue payment plans (>30 days)"},
    "court_email":         {"name": "Court Email Scanner",  "icon": "📧", "category": "monitor",  "desc": "Gmail discharge/exoneration pipeline"},
    "bb_health":           {"name": "iMessage Health",     "icon": "📱", "category": "monitor",  "desc": "BlueBubbles bridge connectivity monitor"},
    "data_retention":      {"name": "Data Retention",      "icon": "🗑️", "category": "monitor",  "desc": "Weekly purge of old low-value records"},
    "geo_intelligence":    {"name": "GPS Tracking",        "icon": "📡", "category": "geo",      "desc": "Traccar device telemetry & geofence monitoring"},
    "findmy_geofence":     {"name": "FindMy Geofence",    "icon": "🔍", "category": "geo",      "desc": "Lee County boundary breach detection"},
    "blog_publisher":      {"name": "Blog Publisher",      "icon": "✍️", "category": "content",  "desc": "Auto-publish scheduled posts to Wix Blog"},
    "wix_sync":            {"name": "Wix CMS Sync",         "icon": "🔗", "category": "content",  "desc": "Sync MongoDB → Wix CMS (IntakeQueue, Cases, CRM)"},
    # Utilitarian automations (Node-RED orchestrated, trigger FastAPI backend)
    "daily_ops_digest":       {"name": "Daily Ops Digest",       "icon": "📰", "category": "monitor",  "desc": "7:30 AM ET: arrest counts, state breakdown, fleet health → Slack #ops-daily"},
    "scraper_health_webhook": {"name": "Scraper Health Webhook",  "icon": "🚨", "category": "monitor",  "desc": "Every 15 min: diff error state; alert new failures → Slack #scraper-alerts"},
    "fta_alert_relay":        {"name": "FTA Alert Relay",         "icon": "🎯", "category": "monitor",  "desc": "Every 30 min: trigger re-arrest detection; relay FTA hits → Slack #fta-alerts"},
    "bond_status_sync":       {"name": "Bond Status Sync",        "icon": "🔄", "category": "lifecycle", "desc": "Every 4 hrs: trigger SignNow poller then Wix sync in sequence"},
    # Node-RED only flows (read-only visibility — no Python loop, NR is the runtime)
    "nr_social_autopilot":    {"name": "Social Auto-Pilot",       "icon": "📱", "category": "content",  "desc": "[Node-RED] Scheduled social posts via Postiz"},
    "nr_court_clerk":         {"name": "The Court Clerk",         "icon": "⚖️", "category": "intel",    "desc": "[Node-RED] Court date polling & calendar sync"},
    "nr_the_closer":          {"name": "The Closer",              "icon": "🤝", "category": "revenue",  "desc": "[Node-RED] Follow-up sequence for warm leads"},
    "nr_morning_briefing":    {"name": "Morning Briefing",        "icon": "☀️", "category": "monitor",  "desc": "[Node-RED] 7 AM briefing digest to Slack"},
    "nr_bounty_hunter":       {"name": "The Bounty Hunter",       "icon": "🔍", "category": "geo",      "desc": "[Node-RED] Fugitive monitoring & Slack alerts"},
    "nr_watchdog":            {"name": "Watchdog",                "icon": "🐕", "category": "monitor",  "desc": "[Node-RED] Every 5 min system health watchdog"},
    "nr_gas_scheduler":       {"name": "GAS Scheduler",           "icon": "⏰", "category": "other",    "desc": "[Node-RED] Google Apps Script cron dispatcher"},
    "nr_intake_pipeline":     {"name": "Intake Pipeline",         "icon": "📥", "category": "lifecycle", "desc": "[Node-RED] Intake form webhook → MongoDB"},
    "nr_revenue_snapshot":    {"name": "Revenue Snapshot",        "icon": "💰", "category": "revenue",  "desc": "[Node-RED] Daily revenue metrics snapshot"},
    "nr_the_scout":           {"name": "The Scout",               "icon": "🥂", "category": "intel",    "desc": "[Node-RED] New arrest feed monitor"},
    "nr_staff_performance":   {"name": "Staff Performance",       "icon": "📊", "category": "monitor",  "desc": "[Node-RED] Agent KPI tracker"},
    "nr_weather_posting":     {"name": "Weather Posting",         "icon": "⛅", "category": "content",  "desc": "[Node-RED] Weather-triggered social content"},
    "nr_review_harvester":    {"name": "Review Harvester",        "icon": "⭐", "category": "content",  "desc": "[Node-RED] Google/Yelp review aggregator"},
    "nr_payment_reminders":   {"name": "Payment Reminders",       "icon": "💳", "category": "revenue",  "desc": "[Node-RED] Overdue payment SMS/iMessage reminders"},
    "nr_no_show_escalation":  {"name": "No-Show Escalation",      "icon": "⚠️", "category": "lifecycle", "desc": "[Node-RED] Court no-show detection & escalation"},
    "nr_whatsapp_campaigns":  {"name": "WhatsApp Campaigns",      "icon": "💬", "category": "revenue",  "desc": "[Node-RED] WhatsApp outreach campaigns"},
    "nr_signnow_tracker":     {"name": "SignNow Tracker",         "icon": "✍️", "category": "lifecycle", "desc": "[Node-RED] SignNow packet status tracker"},
    "nr_bond_renewal":        {"name": "Bond Renewal Reminders",  "icon": "🔔", "category": "lifecycle", "desc": "[Node-RED] Bond renewal date reminders"},
}


# ── GET /api/automation/config — Read current config ───────────────────────
@automation_control_bp.get("/automation/config")
async def get_config():
    """Return the full automation configuration."""
    try:
        db = get_db()
        cfg = await get_automation_config(db)
        return {"success": True, "config": cfg}
    except Exception as exc:
        logger.error("[automation-api] get_config error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── POST /api/automation/config — Update config (partial) ──────────────────
@automation_control_bp.post("/automation/config")
async def set_config(request: Request):
    """Update automation configuration. Accepts partial updates.

    Example body:
    {
        "speed_to_contact.enabled": true,
        "speed_to_contact.min_lead_score": 80,
        "paperwork_chase.nudge_1_hours": 3
    }
    """
    try:
        body = await request.json()
        if not body:
            return JSONResponse({"success": False, "error": "Missing JSON body"}, status_code=400)

        actor = body.pop("actor", "dashboard")
        db = get_db()
        cfg = await update_automation_config(db, body, actor=actor)
        return {"success": True, "config": cfg}
    except Exception as exc:
        logger.error("[automation-api] set_config error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── POST /api/automation/toggle/<key> — Quick on/off ──────────────────────
@automation_control_bp.post("/automation/toggle/{key}")
async def toggle_automation(request: Request, key: str):
    """Toggle a specific service on or off.

    Valid keys: all entries in ALL_SERVICE_KEYS

    Body (optional):
    { "enabled": true/false }

    If no body provided, toggles current state.
    """
    if key not in ALL_SERVICE_KEYS:
        return JSONResponse(status_code=400, content={
            "success": False,
            "error": f"Invalid key: {key}. Valid: {', '.join(sorted(ALL_SERVICE_KEYS))}"
        })

    try:
        body = await request.json() or {}

        if "enabled" in body:
            new_state = bool(body["enabled"])
        else:
            # Toggle current state
            db = get_db()
            currently_enabled = await is_enabled(db, key)
            new_state = not currently_enabled

        db = get_db()
        cfg = await update_automation_config(
            db,
            {f"{key}.enabled": new_state},
            actor=body.get("actor", "dashboard"),
        )

        state_label = "🟢 ENABLED" if new_state else "🔴 DISABLED"
        logger.info("☘️  Service [%s] → %s", key, state_label)

        return {
            "success": True,
            "key": key,
            "enabled": new_state,
            "config": cfg,
        }
    except Exception as exc:
        logger.error("[automation-api] toggle error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── POST /api/automation/trigger/<key> — Manual one-shot run ──────────────
@automation_control_bp.post("/automation/trigger/{key}")
async def trigger_automation(key: str):
    """Manually trigger one immediate cycle of a background service.

    This sets an asyncio.Event that the cron loop watches.  The loop will
    skip its current sleep and execute one cycle immediately, then return
    to its normal schedule.

    If the service has no registered trigger event (e.g. it was never
    started because it is disabled), the endpoint still returns success
    and logs a warning — the service will run on its next scheduled cycle.

    Valid keys: all entries in ALL_SERVICE_KEYS
    """
    if key not in ALL_SERVICE_KEYS:
        return JSONResponse(status_code=400, content={
            "success": False,
            "error": f"Invalid key: {key}. Valid: {', '.join(sorted(ALL_SERVICE_KEYS))}"
        })

    event = TRIGGER_EVENTS.get(key)
    if event is not None:
        event.set()
        logger.info("☘️  Manual trigger fired for service [%s]", key)
        triggered = True
    else:
        # Service loop not yet running (disabled at boot) — log and acknowledge
        logger.warning(
            "[automation-api] trigger: no event registered for [%s] — "
            "service may be disabled or not yet started", key
        )
        triggered = False

    # Log the manual trigger to automation_run_log so the UI shows "triggered"
    try:
        db = get_db()
        await db["automation_run_log"].insert_one({
            "automation": key,
            "run_at": datetime.now(timezone.utc),
            "result": {"manual_trigger": True, "event_fired": triggered},
            "triggered_by": "dashboard",
        })
    except Exception:
        pass  # Non-fatal — don't fail the response if logging fails

    meta = SERVICE_META.get(key, {})
    return {
        "success": True,
        "key": key,
        "name": meta.get("name", key),
        "triggered": triggered,
        "message": (
            f"▶ {meta.get('name', key)} triggered — running next cycle immediately"
            if triggered
            else f"⚠ {meta.get('name', key)} has no active loop (service may be disabled)"
        ),
    }


# ── GET /api/automation/status — Full runtime status ─────────────────────
@automation_control_bp.get("/automation/status")
async def get_status():
    """Return the current runtime status of ALL services.

    For each service: enabled state, interval, last run time, last result.
    Also includes service metadata (name, icon, category, description).
    """
    try:
        db = get_db()
        cfg = await get_automation_config(db)
        status = {}

        for key in sorted(ALL_SERVICE_KEYS):
            section = cfg.get(key, {})
            meta = SERVICE_META.get(key, {})

            # Query last run log
            last_run = await db["automation_run_log"].find_one(
                {"automation": key},
                sort=[("run_at", -1)],
            )

            # Null-guard: run_at may be None or not a datetime
            last_run_at = None
            if last_run and last_run.get("run_at"):
                raw = last_run["run_at"]
                try:
                    last_run_at = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
                except Exception:
                    last_run_at = None

            # Classify runtime type for frontend badge rendering
            _nr_only = key.startswith("nr_")
            _nr_orchestrated = key in {
                "daily_ops_digest", "scraper_health_webhook",
                "fta_alert_relay", "bond_status_sync",
            }

            status[key] = {
                "enabled": section.get("enabled", False),
                "interval_seconds": section.get("interval_seconds", 0),
                "last_run_at": last_run_at,
                "last_result": last_run.get("result") if last_run else None,
                "last_error": last_run.get("error") if last_run else None,
                "has_trigger": key in TRIGGER_EVENTS and not _nr_only,
                "nr_only": _nr_only,
                "nr_orchestrated": _nr_orchestrated,
                # Metadata
                "name": meta.get("name", key),
                "icon": meta.get("icon", "⚙️"),
                "category": meta.get("category", "other"),
                "description": meta.get("desc", ""),
            }

        nr_count = sum(1 for k in ALL_SERVICE_KEYS if k.startswith("nr_"))
        return {
            "success": True,
            "status": status,
            "config": cfg,
            "service_count": len(ALL_SERVICE_KEYS),
            "nr_online": True,   # Node-RED health check can update this via webhook
            "nr_flow_count": nr_count + 4,  # NR-only + 4 utilitarian flows
        }
    except Exception as exc:
        logger.error("[automation-api] get_status error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
