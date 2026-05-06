"""
ShamrockLeads — Automation Control API
=======================================
Dashboard endpoints for controlling background revenue automations.

GET  /api/automation/config        → Current config (all toggles)
POST /api/automation/config        → Update config (partial updates)
POST /api/automation/toggle/<key>  → Quick enable/disable a specific automation
GET  /api/automation/status        → Current runtime status of all crons

All endpoints require authentication (existing session-based auth).
"""
import logging
from quart import Blueprint, request, jsonify, current_app

from dashboard.services.automation_config import (
    get_automation_config,
    update_automation_config,
    is_enabled,
)

logger = logging.getLogger(__name__)

automation_bp = Blueprint("automation", __name__)


# ── GET /api/automation/config — Read current config ───────────────────────
@automation_bp.route("/automation/config", methods=["GET"])
async def get_config():
    """Return the full automation configuration."""
    try:
        cfg = await get_automation_config(current_app.db)
        return jsonify({"success": True, "config": cfg})
    except Exception as exc:
        logger.error("[automation-api] get_config error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── POST /api/automation/config — Update config (partial) ──────────────────
@automation_bp.route("/automation/config", methods=["POST"])
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
@automation_bp.route("/automation/toggle/<key>", methods=["POST"])
async def toggle_automation(key: str):
    """Toggle a specific automation on or off.

    Valid keys: speed_to_contact, paperwork_chase, intake_recovery, auto_reply, findmy_geofence

    Body (optional):
    { "enabled": true/false }

    If no body provided, toggles current state.
    """
    valid_keys = {"speed_to_contact", "paperwork_chase", "intake_recovery", "auto_reply", "findmy_geofence"}
    if key not in valid_keys:
        return jsonify({
            "success": False,
            "error": f"Invalid key: {key}. Valid: {', '.join(sorted(valid_keys))}"
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
        logger.info("☘️  Automation [%s] → %s", key, state_label)

        return jsonify({
            "success": True,
            "key": key,
            "enabled": new_state,
            "config": cfg,
        })
    except Exception as exc:
        logger.error("[automation-api] toggle error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── GET /api/automation/status — Runtime status ─────────────────────────────
@automation_bp.route("/automation/status", methods=["GET"])
async def get_status():
    """Return the current runtime status of all automation crons.

    Includes last run time, last result, and whether the cron is active.
    """
    try:
        cfg = await get_automation_config(current_app.db)

        # Get last run logs from MongoDB
        db = current_app.db
        status = {}

        for key in ["speed_to_contact", "paperwork_chase", "intake_recovery"]:
            section = cfg.get(key, {})
            last_run = await db["automation_run_log"].find_one(
                {"automation": key},
                sort=[("run_at", -1)],
            )
            status[key] = {
                "enabled": section.get("enabled", False),
                "interval_seconds": section.get("interval_seconds", 0),
                "last_run_at": last_run["run_at"].isoformat() if last_run and last_run.get("run_at") else None,
                "last_result": last_run.get("result") if last_run else None,
            }

        return jsonify({"success": True, "status": status, "config": cfg})
    except Exception as exc:
        logger.error("[automation-api] get_status error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
