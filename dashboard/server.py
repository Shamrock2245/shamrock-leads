"""
ShamrockLeads Dashboard Server
Serves the static dashboard HTML and provides a health/status JSON API.
Runs on port 8088.
"""
import os
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
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


def _get_mongo_db():
    """Return a pymongo db handle (caller must close client)."""
    from pymongo import MongoClient
    client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=5000)
    db = client[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]
    return client, db


# ── Static routes ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/mobile")
@app.route("/mobile.html")
def mobile():
    return send_from_directory(".", "mobile.html")


# ── Health / Status ────────────────────────────────────────────

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


# ── MongoDB Stats ──────────────────────────────────────────────

@app.route("/api/mongo-stats")
def api_mongo_stats():
    """Live MongoDB stats - county record counts and lead scores."""
    try:
        client, db = _get_mongo_db()

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

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_hot = list(db.arrests.find(
            {"lead_score": {"$gte": 70}, "scraped_at": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "bond_type": 1}
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


# ── Leads Detail API ───────────────────────────────────────────

@app.route("/api/leads")
def api_leads():
    """
    Full leads endpoint with filtering, sorting, and pagination.
    Query params:
      - status: Hot|Warm|Cold|Disqualified (default: all)
      - county: filter by county name
      - search: text search on full_name or charges
      - sort: lead_score|bond_amount|booking_date|full_name (default: lead_score)
      - order: desc|asc (default: desc)
      - page: 1-indexed page number (default: 1)
      - limit: records per page (default: 50, max: 200)
      - min_bond: minimum bond amount filter
      - custody: true to show only "In Custody"
    """
    try:
        client, db = _get_mongo_db()

        # Build query
        query = {}

        # Status filter
        status_filter = request.args.get("status", "").strip()
        if status_filter:
            query["lead_status"] = status_filter

        # County filter
        county_filter = request.args.get("county", "").strip()
        if county_filter:
            query["county"] = county_filter

        # Custody filter
        if request.args.get("custody", "").lower() == "true":
            query["status"] = "In Custody"

        # Min bond filter
        min_bond = request.args.get("min_bond", "").strip()
        if min_bond:
            try:
                query["bond_amount"] = {"$gte": float(min_bond)}
            except ValueError:
                pass

        # Text search on name or charges
        search = request.args.get("search", "").strip()
        if search:
            import re
            pattern = re.compile(re.escape(search), re.IGNORECASE)
            query["$or"] = [
                {"full_name": {"$regex": pattern}},
                {"charges": {"$regex": pattern}},
            ]

        # Sorting
        sort_field = request.args.get("sort", "lead_score").strip()
        sort_order = -1 if request.args.get("order", "desc").strip() == "desc" else 1
        sort_map = {
            "lead_score": "lead_score",
            "bond_amount": "bond_amount",
            "booking_date": "booking_date",
            "full_name": "full_name",
            "county": "county",
        }
        mongo_sort = sort_map.get(sort_field, "lead_score")

        # Pagination
        page = max(1, int(request.args.get("page", 1)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        skip = (page - 1) * limit

        # Project only needed fields (fast)
        projection = {
            "_id": 0,
            "full_name": 1,
            "first_name": 1,
            "last_name": 1,
            "booking_number": 1,
            "county": 1,
            "charges": 1,
            "bond_amount": 1,
            "bond_type": 1,
            "lead_score": 1,
            "lead_status": 1,
            "status": 1,
            "arrest_date": 1,
            "booking_date": 1,
            "court_date": 1,
            "court_location": 1,
            "case_number": 1,
            "dob": 1,
            "sex": 1,
            "race": 1,
            "address": 1,
            "detail_url": 1,
            "facility": 1,
            "mugshot_url": 1,
            "scraped_at": 1,
        }

        # Execute
        total_matching = db.arrests.count_documents(query)
        cursor = db.arrests.find(query, projection).sort(mongo_sort, sort_order).skip(skip).limit(limit)
        leads = list(cursor)

        # Get available counties for filter dropdown
        counties_list = sorted(db.arrests.distinct("county"))

        client.close()

        return jsonify({
            "leads": leads,
            "total": total_matching,
            "page": page,
            "limit": limit,
            "pages": (total_matching + limit - 1) // limit,
            "counties": counties_list,
            "query": {
                "status": status_filter,
                "county": county_filter,
                "search": search,
                "sort": sort_field,
                "order": "desc" if sort_order == -1 else "asc",
            },
        })
    except Exception as e:
        logger.error(f"Leads API error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads/<booking_number>")
def api_lead_detail(booking_number):
    """Get full detail for a single lead by booking number."""
    try:
        client, db = _get_mongo_db()
        doc = db.arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not doc:
            # Try as integer
            try:
                doc = db.arrests.find_one({"booking_number": int(booking_number)}, {"_id": 0})
            except (ValueError, TypeError):
                pass
        client.close()
        if not doc:
            return jsonify({"error": "Not found"}), 404
        return jsonify(doc)
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
