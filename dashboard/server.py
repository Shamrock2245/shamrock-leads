"""
ShamrockLeads Dashboard Server
Serves the static dashboard HTML and provides a health/status JSON API.
Runs on port 8088.
"""
import os
import json
import logging
import threading
from datetime import datetime, timezone
from flask import Flask, send_from_directory, jsonify, request

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=".")

# Shared state updated by the scraper engine
_status = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "scrapers": {},
    "total_scraped": 0,
    "total_hot_leads": 0,
    "total_warm_leads": 0,
    "cycle_count": 0,
}
_lock = threading.Lock()


def update_scraper_status(county, records, hot, warm, cold=0,
                          disqualified=0, duration=0, status="ok",
                          error=None):
    """Called by scraper engine to update dashboard state."""
    with _lock:
        existing = _status["scrapers"].get(county, {})
        run_count = existing.get("run_count", 0) + 1
        total_records = existing.get("total_records", 0) + records

        _status["scrapers"][county] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "records": records,
            "hot_leads": hot,
            "warm_leads": warm,
            "cold_leads": cold,
            "disqualified": disqualified,
            "duration_seconds": round(duration, 1),
            "status": status,
            "error": error,
            "run_count": run_count,
            "total_records": total_records,
        }
        _status["total_scraped"] = sum(s.get("total_records", 0) for s in _status["scrapers"].values())
        _status["total_hot_leads"] = sum(s.get("hot_leads", 0) for s in _status["scrapers"].values())
        _status["total_warm_leads"] = sum(s.get("warm_leads", 0) for s in _status["scrapers"].values())
        _status["cycle_count"] += 1


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/mobile")
@app.route("/mobile.html")
def mobile():
    return send_from_directory(".", "mobile.html")


@app.route("/health")
def health():
    with _lock:
        active = sum(1 for s in _status["scrapers"].values() if s.get("status") == "ok")
        errored = sum(1 for s in _status["scrapers"].values() if s.get("status") != "ok")
    return jsonify({
        "status": "ok",
        "uptime_since": _status["started_at"],
        "active_scrapers": active,
        "errored_scrapers": errored,
    })


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_status)


@app.route("/api/scrapers")
def api_scrapers():
    """Per-scraper breakdown for the live dashboard."""
    with _lock:
        scrapers = []
        for county, data in sorted(_status["scrapers"].items()):
            scrapers.append({"county": county, **data})
        return jsonify({
            "scrapers": scrapers,
            "count": len(scrapers),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })


@app.route("/api/mongo-stats")
def api_mongo_stats():
    """Live MongoDB stats - county record counts and lead scores."""
    try:
        from pymongo import MongoClient
        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=3000)
        db = client[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]

        total = db.arrests.count_documents({})

        county_pipeline = [
            {"$group": {"_id": "$county", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        by_county = {doc["_id"]: doc["count"] for doc in db.arrests.aggregate(county_pipeline)}

        hot = db.arrests.count_documents({"lead_score": {"$gte": 70}})
        warm = db.arrests.count_documents({"lead_score": {"$gte": 40, "$lt": 70}})
        cold = db.arrests.count_documents({"lead_score": {"$gte": 20, "$lt": 40}})
        disq = db.arrests.count_documents({"lead_score": {"$lt": 20}})

        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_hot = list(db.arrests.find(
            {"lead_score": {"$gte": 70}, "scraped_at": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1, "bond_amount": 1, "lead_score": 1}
        ).sort("lead_score", -1).limit(20))

        client.close()

        return jsonify({
            "total_records": total,
            "by_county": by_county,
            "scores": {"hot": hot, "warm": warm, "cold": cold, "disqualified": disq},
            "recent_hot_leads": recent_hot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def start_dashboard_server(port=8088):
    """Start the dashboard in a background thread."""
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
        name="dashboard-server",
    )
    thread.start()
    logger.info(f"Dashboard server started on port {port}")
    return thread
