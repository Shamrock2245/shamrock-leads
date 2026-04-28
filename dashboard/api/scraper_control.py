"""
ShamrockLeads — Scraper Control API Blueprint
Endpoints:
  POST /api/scraper/run-now          — Trigger immediate run for one county
  POST /api/scraper/run-all          — Trigger immediate run for all counties
  GET  /api/scraper/scheduler-status — Current scheduler / trigger status
  POST /api/scraper/enable           — Enable a county scraper
  POST /api/scraper/disable          — Disable (pause) a county scraper
  GET  /api/scraper/logs/<county>    — Recent run log entries for a county
  POST /api/scraper/health-check     — Trigger a URL pre-flight check for a county
  GET  /api/scraper/config           — List all counties with enabled/disabled state

Uses a MongoDB 'scraper_triggers' collection as a message bus between the
dashboard container and the scraper engine container (they share MongoDB but
not in-process state). The scraper engine polls this collection and executes
the requested runs.
"""
from datetime import datetime, timezone, timedelta
from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection, REGISTERED_COUNTIES

scraper_control_bp = Blueprint("scraper_control", __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/scraper/run-now
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/run-now", methods=["POST"])
async def api_run_now():
    """
    Trigger an immediate scraper run for a specific county.
    POST body: {"county": "Lee"}
    Writes a trigger document to MongoDB; the scraper engine polls and executes.
    """
    data = await request.get_json(silent=True) or {}
    county = (data.get("county") or "").strip()
    if not county:
        return jsonify({"error": "county is required"}), 400

    matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
    if not matched:
        matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
    if not matched:
        return jsonify({"error": f"County '{county}' not found in registered scrapers"}), 404

    triggers = get_collection("scraper_triggers")
    now = datetime.now(timezone.utc)
    await triggers.update_one(
        {"county": matched},
        {"$set": {
            "county": matched,
            "requested_at": now,
            "status": "pending",
            "requested_by": "dashboard",
        }},
        upsert=True,
    )
    return jsonify({
        "ok": True,
        "county": matched,
        "message": f"Run trigger queued for {matched}. The scraper engine will execute it within 60 seconds.",
        "requested_at": now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/scraper/run-all
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/run-all", methods=["POST"])
async def api_run_all():
    """
    Trigger an immediate run for ALL registered scrapers.
    Writes trigger documents for all registered counties.
    """
    triggers = get_collection("scraper_triggers")
    now = datetime.now(timezone.utc)
    for county in REGISTERED_COUNTIES:
        await triggers.update_one(
            {"county": county},
            {"$set": {
                "county": county,
                "requested_at": now,
                "status": "pending",
                "requested_by": "dashboard_run_all",
            }},
            upsert=True,
        )
    return jsonify({
        "ok": True,
        "triggered": len(REGISTERED_COUNTIES),
        "message": f"Run triggers queued for all {len(REGISTERED_COUNTIES)} counties.",
        "requested_at": now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/scraper/scheduler-status
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/scheduler-status")
async def api_scheduler_status():
    """
    Returns the current scheduler status from MongoDB.
    Reads from scraper_status collection (written by base_scraper after each run)
    plus any pending triggers.
    """
    scraper_status_col = get_collection("scraper_status")
    triggers = get_collection("scraper_triggers")
    scraper_config_col = get_collection("scraper_config")

    status_map = {}
    async for doc in scraper_status_col.find({}, {"_id": 0}):
        county = doc.get("county")
        if county:
            status_map[county] = doc

    config_map = {}
    async for doc in scraper_config_col.find({}, {"_id": 0}):
        county = doc.get("county")
        if county:
            config_map[county] = doc

    pending_triggers = []
    async for doc in triggers.find({"status": "pending"}, {"_id": 0}):
        pending_triggers.append(doc.get("county"))

    total_registered = len(REGISTERED_COUNTIES)
    active = sum(1 for c in REGISTERED_COUNTIES if status_map.get(c, {}).get("status") == "ok")
    errors = sum(1 for c in REGISTERED_COUNTIES if status_map.get(c, {}).get("status") == "error")
    never_run = sum(1 for c in REGISTERED_COUNTIES if c not in status_map)
    disabled = sum(1 for c in REGISTERED_COUNTIES if config_map.get(c, {}).get("enabled") is False)

    return jsonify({
        "total_registered": total_registered,
        "active": active,
        "errors": errors,
        "never_run": never_run,
        "disabled": disabled,
        "pending_triggers": pending_triggers,
        "pending_count": len(pending_triggers),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/scraper/enable
#  POST /api/scraper/disable
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/enable", methods=["POST"])
async def api_scraper_enable():
    """Enable a previously disabled county scraper."""
    return await _set_scraper_enabled(True)


@scraper_control_bp.route("/scraper/disable", methods=["POST"])
async def api_scraper_disable():
    """Disable (pause) a county scraper so it won't be auto-scheduled."""
    return await _set_scraper_enabled(False)


async def _set_scraper_enabled(enabled: bool):
    data = await request.get_json(silent=True) or {}
    county = (data.get("county") or "").strip()
    if not county:
        return jsonify({"error": "county is required"}), 400

    matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
    if not matched:
        matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
    if not matched:
        return jsonify({"error": f"County '{county}' not found"}), 404

    scraper_config_col = get_collection("scraper_config")
    now = datetime.now(timezone.utc)
    await scraper_config_col.update_one(
        {"county": matched},
        {"$set": {
            "county": matched,
            "enabled": enabled,
            "updated_at": now,
            "updated_by": data.get("agent", "dashboard"),
            "reason": data.get("reason", ""),
        }},
        upsert=True,
    )
    action = "enabled" if enabled else "disabled"
    return jsonify({
        "ok": True,
        "county": matched,
        "enabled": enabled,
        "message": f"{matched} scraper {action}.",
        "updated_at": now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/scraper/logs/<county>
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/logs/<county>")
async def api_scraper_logs(county: str):
    """
    Return recent run log entries for a specific county.
    Reads from scraper_run_log collection (written by base_scraper after each run).
    Falls back to scraper_status if no detailed log exists.
    """
    matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
    if not matched:
        matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
    if not matched:
        return jsonify({"error": f"County '{county}' not found"}), 404

    limit = int(request.args.get("limit", 20))
    scraper_run_log = get_collection("scraper_run_log")
    scraper_status_col = get_collection("scraper_status")

    logs = []
    async for doc in scraper_run_log.find(
        {"county": matched},
        {"_id": 0}
    ).sort("started_at", -1).limit(limit):
        for k, v in list(doc.items()):
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        logs.append(doc)

    # If no detailed log, synthesize from scraper_status
    if not logs:
        live = await scraper_status_col.find_one({"county": matched}, {"_id": 0})
        if live:
            for k, v in list(live.items()):
                if isinstance(v, datetime):
                    live[k] = v.isoformat()
            logs = [live]

    return jsonify({
        "county": matched,
        "logs": logs,
        "count": len(logs),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/scraper/health-check
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/health-check", methods=["POST"])
async def api_scraper_health_check():
    """
    Trigger a URL pre-flight health check for a specific county.
    Writes a 'health_check' trigger; the scraper engine performs a HEAD request
    against the county's roster_url and reports back to scraper_status.
    """
    data = await request.get_json(silent=True) or {}
    county = (data.get("county") or "").strip()
    if not county:
        return jsonify({"error": "county is required"}), 400

    matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
    if not matched:
        matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
    if not matched:
        return jsonify({"error": f"County '{county}' not found"}), 404

    triggers = get_collection("scraper_triggers")
    now = datetime.now(timezone.utc)
    await triggers.update_one(
        {"county": matched, "type": "health_check"},
        {"$set": {
            "county": matched,
            "type": "health_check",
            "requested_at": now,
            "status": "pending",
            "requested_by": "dashboard_health_check",
        }},
        upsert=True,
    )
    return jsonify({
        "ok": True,
        "county": matched,
        "message": f"Health check queued for {matched}. Result will appear in scraper status within 60s.",
        "requested_at": now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/scraper/config
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/config")
async def api_scraper_config():
    """
    Return configuration for all registered counties:
    enabled/disabled state, interval, last updated.
    """
    scraper_config_col = get_collection("scraper_config")
    config_map = {}
    async for doc in scraper_config_col.find({}, {"_id": 0}):
        county = doc.get("county")
        if county:
            for k, v in list(doc.items()):
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            config_map[county] = doc

    result = []
    for county in sorted(REGISTERED_COUNTIES):
        cfg = config_map.get(county, {})
        result.append({
            "county": county,
            "enabled": cfg.get("enabled", True),
            "interval_minutes": cfg.get("interval_minutes", 60),
            "updated_at": cfg.get("updated_at", ""),
            "reason": cfg.get("reason", ""),
        })

    return jsonify({
        "counties": result,
        "total": len(result),
        "disabled_count": sum(1 for r in result if not r["enabled"]),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/scraper/custody-recheck
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/custody-recheck", methods=["POST"])
async def api_custody_recheck():
    """
    Trigger an on-demand custody verification for a county or single booking.
    The scraper engine re-checks each in-custody defendant against the live
    jail roster and writes diffs to the custody_rechecks collection.

    POST body:
      { "county": "Lee" }                    — recheck all in-custody for county
      { "booking_number": "2025-001234" }     — recheck a single defendant
    """
    data = await request.get_json(silent=True) or {}
    county = (data.get("county") or "").strip()
    booking_number = (data.get("booking_number") or "").strip()

    if not county and not booking_number:
        return jsonify({"error": "county or booking_number is required"}), 400

    # Resolve county name
    if county:
        matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
        if not matched:
            matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
        if not matched:
            return jsonify({"error": f"County '{county}' not found"}), 404
        county = matched

    # If only booking_number provided, look up its county
    if booking_number and not county:
        arrests = get_collection("arrests")
        doc = await arrests.find_one(
            {"booking_number": booking_number},
            {"county": 1}
        )
        if doc:
            county = doc.get("county", "")
        if not county:
            return jsonify({"error": f"No record found for booking {booking_number}"}), 404

    triggers = get_collection("scraper_triggers")
    now = datetime.now(timezone.utc)

    trigger_doc = {
        "county": county,
        "type": "custody_recheck",
        "requested_at": now,
        "status": "pending",
        "requested_by": "dashboard_custody_recheck",
    }
    if booking_number:
        trigger_doc["booking_number"] = booking_number
        trigger_doc["mode"] = "single"
    else:
        trigger_doc["mode"] = "county"

    result = await triggers.insert_one(trigger_doc)
    trigger_id = str(result.inserted_id)

    return jsonify({
        "ok": True,
        "trigger_id": trigger_id,
        "county": county,
        "mode": trigger_doc["mode"],
        "message": f"Custody recheck queued for {county}. Results will appear within 30–120 seconds.",
        "requested_at": now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/scraper/custody-recheck/results
# ─────────────────────────────────────────────────────────────────────────────
@scraper_control_bp.route("/scraper/custody-recheck/results")
async def api_custody_recheck_results():
    """
    Poll for custody recheck results.
    Query params:
      ?trigger_id=...   — specific trigger
      ?county=Lee       — latest results for county
    Returns trigger status + list of diffs.
    """
    trigger_id = request.args.get("trigger_id", "").strip()
    county = request.args.get("county", "").strip()

    if not trigger_id and not county:
        return jsonify({"error": "trigger_id or county is required"}), 400

    triggers = get_collection("scraper_triggers")
    rechecks = get_collection("custody_rechecks")

    # Get the trigger status
    trigger_doc = None
    if trigger_id:
        try:
            from bson import ObjectId
            trigger_doc = await triggers.find_one(
                {"_id": ObjectId(trigger_id)},
                {"_id": 0}
            )
        except Exception:
            pass
    elif county:
        matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
        if matched:
            county = matched
        trigger_doc = await triggers.find_one(
            {"county": county, "type": "custody_recheck"},
            {"_id": 0},
            sort=[("requested_at", -1)],
        )

    if not trigger_doc:
        return jsonify({"status": "not_found", "diffs": [], "total_checked": 0})

    # Serialize datetimes
    for k, v in list(trigger_doc.items()):
        if isinstance(v, datetime):
            trigger_doc[k] = v.isoformat()

    trigger_status = trigger_doc.get("status", "pending")

    # Get diff results
    query = {"county": trigger_doc.get("county", county)}
    if trigger_id:
        query["trigger_id"] = trigger_id

    diffs = []
    async for doc in rechecks.find(
        query,
        {"_id": 0}
    ).sort("checked_at", -1).limit(200):
        for k, v in list(doc.items()):
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        diffs.append(doc)

    # Summary stats
    changes_found = sum(1 for d in diffs if d.get("changes"))
    not_found = sum(1 for d in diffs if not d.get("source_found", True))

    return jsonify({
        "status": trigger_status,
        "trigger": trigger_doc,
        "diffs": diffs,
        "total_checked": trigger_doc.get("total_checked", 0),
        "changes_found": changes_found,
        "not_found_count": not_found,
        "county": trigger_doc.get("county", ""),
    })
