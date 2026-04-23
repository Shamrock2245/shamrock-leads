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
from flask import Flask, send_from_directory, jsonify

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=".")

# Shared state updated by the scraper engine
_status = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "scrapers": {},
    "total_scraped": 0,
    "total_hot_leads": 0,
}
_lock = threading.Lock()


def update_scraper_status(county: str, records: int, hot: int, warm: int, status: str = "ok"):
    """Called by scraper engine to update dashboard state."""
    with _lock:
        _status["scrapers"][county] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "records": records,
            "hot_leads": hot,
            "warm_leads": warm,
            "status": status,
        }
        _status["total_scraped"] = sum(s["records"] for s in _status["scrapers"].values())
        _status["total_hot_leads"] = sum(s["hot_leads"] for s in _status["scrapers"].values())


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/mobile")
@app.route("/mobile.html")
def mobile():
    return send_from_directory(".", "mobile.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "uptime_since": _status["started_at"]})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_status)


def start_dashboard_server(port: int = 8088):
    """Start the dashboard in a background thread."""
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
        name="dashboard-server",
    )
    thread.start()
    logger.info(f"Dashboard server started on port {port}")
    return thread
