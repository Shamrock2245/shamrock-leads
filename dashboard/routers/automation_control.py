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
    # Intelligence Pipeline
    "docket_monitor", "court_intel", "nlp_enrichment",
    # Monitoring & Compliance
    "court_reminders", "rearrest_detection", "delinquency_scanner",
    "court_email", "bb_health", "data_retention",
    # Geo Intelligence
    "geo_intelligence", "findmy_geofence",
    # Content
    "blog_publisher", "wix_sync",
}

# Service metadata for frontend rendering
SERVICE_META = {
    "speed_to_contact":    {"name": "Speed-to-Contact",    "icon": "🚀", "category": "revenue",  "desc": "Auto-outreach for hot leads via iMessage"},
    "paperwork_chase":     {"name": "Paperwork Chase",     "icon": "📋", "category": "revenue",  "desc": "Auto-nudge unsigned SignNow packets"},
    "intake_recovery":     {"name": "Intake Recovery",     "icon": "🔄", "category": "revenue",  "desc": "Recover abandoned intake submissions"},
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
}


# ── GET /api/automation/config — Read current config ───────────────────────
@automation_control_bp.route("/automation/config", methods=["GET"])
async def get_config():
    """Return the full automation configuration."""
    try:
        cfg = await get_automation_config(current_app.db)
        return jsonify({"success": True, "config": cfg})
    except Exception as exc:
        logger.error("[automation-api] get_config error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── POST /api/automation/config — Update config (partial) ──────────────────
@automation_control_bp.route("/automation/config", methods=["POST"])
async def set_config():
    """Update automation configuration. Accepts partial updates.

    Example body:
    {
        "speed_to_contact.enabled": true,
        "speed_to_contact.min_lead_score": 80,
        "paperwork_chase.nudge_1_hours": 3
    }
    """
    try:
        body = await request.get_json()
        if not body:
            return jsonify({"success": False, "error": "Missing JSON body"}), 400

        actor = body.pop("actor", "dashboard")
        cfg = await update_automation_config(current_app.db, body, actor=actor)
        return jsonify({"success": True, "config": cfg})
    except Exception as exc:
        logger.error("[automation-api] set_config error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── POST /api/automation/toggle/<key> — Quick on/off ──────────────────────
@automation_control_bp.route("/automation/toggle/<key>", methods=["POST"])
async def toggle_automation(key: str):
    """Toggle a specific service on or off.

    Valid keys: all entries in ALL_SERVICE_KEYS

    Body (optional):
    { "enabled": true/false }

    If no body provided, toggles current state.
    """
    if key not in ALL_SERVICE_KEYS:
        return jsonify({
            "success": False,
            "error": f"Invalid key: {key}. Valid: {', '.join(sorted(ALL_SERVICE_KEYS))}"
        }), 400

    try:
        body = await request.get_json(silent=True) or {}

        if "enabled" in body:
            new_state = bool(body["enabled"])
        else:
            # Toggle current state
            currently_enabled = await is_enabled(current_app.db, key)
            new_state = not currently_enabled

        cfg = await update_automation_config(
            current_app.db,
            {f"{key}.enabled": new_state},
            actor=body.get("actor", "dashboard"),
        )

        state_label = "🟢 ENABLED" if new_state else "🔴 DISABLED"
        logger.info("☘️  Service [%s] → %s", key, state_label)

        return jsonify({
            "success": True,
            "key": key,
            "enabled": new_state,
            "config": cfg,
        })
    except Exception as exc:
        logger.error("[automation-api] toggle error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── POST /api/automation/trigger/<key> — Manual one-shot run ──────────────
@automation_control_bp.route("/automation/trigger/<key>", methods=["POST"])
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
        return jsonify({
            "success": False,
            "error": f"Invalid key: {key}. Valid: {', '.join(sorted(ALL_SERVICE_KEYS))}"
        }), 400

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
        await current_app.db["automation_run_log"].insert_one({
            "automation": key,
            "run_at": datetime.now(timezone.utc),
            "result": {"manual_trigger": True, "event_fired": triggered},
            "triggered_by": "dashboard",
        })
    except Exception:
        pass  # Non-fatal — don't fail the response if logging fails

    meta = SERVICE_META.get(key, {})
    return jsonify({
        "success": True,
        "key": key,
        "name": meta.get("name", key),
        "triggered": triggered,
        "message": (
            f"▶ {meta.get('name', key)} triggered — running next cycle immediately"
            if triggered
            else f"⚠ {meta.get('name', key)} has no active loop (service may be disabled)"
        ),
    })


# ── GET /api/automation/status — Full runtime status ─────────────────────
@automation_control_bp.route("/automation/status", methods=["GET"])
async def get_status():
    """Return the current runtime status of ALL services.

    For each service: enabled state, interval, last run time, last result.
    Also includes service metadata (name, icon, category, description).
    """
    try:
        cfg = await get_automation_config(current_app.db)
        db = current_app.db
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

            status[key] = {
                "enabled": section.get("enabled", False),
                "interval_seconds": section.get("interval_seconds", 0),
                "last_run_at": last_run_at,
                "last_result": last_run.get("result") if last_run else None,
                "last_error": last_run.get("error") if last_run else None,
                "has_trigger": key in TRIGGER_EVENTS,
                # Metadata
                "name": meta.get("name", key),
                "icon": meta.get("icon", "⚙️"),
                "category": meta.get("category", "other"),
                "description": meta.get("desc", ""),
            }

        return jsonify({
            "success": True,
            "status": status,
            "config": cfg,
            "service_count": len(ALL_SERVICE_KEYS),
        })
    except Exception as exc:
        logger.error("[automation-api] get_status error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
