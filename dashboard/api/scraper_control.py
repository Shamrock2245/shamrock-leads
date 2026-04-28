"""
ShamrockLeads — Scraper Control API Blueprint
Endpoints: /api/scraper/run-now, /api/scraper/run-all, /api/scraper/scheduler-status

Uses a MongoDB 'scraper_triggers' collection as a message bus between the
dashboard container and the scraper engine container (they share MongoDB but
not in-process state). The scraper engine polls this collection and executes
the requested runs.
"""

from datetime import datetime, timezone
from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection, REGISTERED_COUNTIES

scraper_control_bp = Blueprint("scraper_control", __name__)


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

    # Normalize county name
    matched = next((c for c in REGISTERED_COUNTIES if c.lower() == county.lower()), None)
    if not matched:
        # Try partial match
        matched = next((c for c in REGISTERED_COUNTIES if county.lower() in c.lower()), None)
    if not matched:
        return jsonify({"error": f"County '{county}' not found in registered scrapers"}), 404

    triggers = get_collection("scraper_triggers")
    now = datetime.now(timezone.utc)

    # Upsert a trigger document — the scraper engine will pick this up
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


@scraper_control_bp.route("/scraper/run-all", methods=["POST"])
async def api_run_all():
    """
    Trigger an immediate run for ALL registered scrapers.
    Writes trigger documents for all 49 counties.
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


@scraper_control_bp.route("/scraper/scheduler-status")
async def api_scheduler_status():
    """
    Returns the current scheduler status from MongoDB.
    Reads from scraper_status collection (written by base_scraper after each run)
    plus any pending triggers.
    """
    scraper_status_col = get_collection("scraper_status")
    triggers = get_collection("scraper_triggers")

    status_map = {}
    async for doc in scraper_status_col.find({}, {"_id": 0}):
        county = doc.get("county")
        if county:
            status_map[county] = doc

    pending_triggers = []
    async for doc in triggers.find({"status": "pending"}, {"_id": 0}):
        pending_triggers.append(doc.get("county"))

    total_registered = len(REGISTERED_COUNTIES)
    active = sum(1 for c in REGISTERED_COUNTIES if status_map.get(c, {}).get("status") == "ok")
    errors = sum(1 for c in REGISTERED_COUNTIES if status_map.get(c, {}).get("status") == "error")
    never_run = sum(1 for c in REGISTERED_COUNTIES if c not in status_map)

    return jsonify({
        "total_registered": total_registered,
        "active": active,
        "errors": errors,
        "never_run": never_run,
        "pending_triggers": pending_triggers,
        "pending_count": len(pending_triggers),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
