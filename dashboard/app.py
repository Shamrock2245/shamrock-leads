"""
ShamrockLeads — Local Intelligence Dashboard
Flask server with MongoDB API endpoints.

Run:  python dashboard/app.py
Then: open http://localhost:5050
"""

import csv
import io
import os
import re as re_mod
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request, Response
from pymongo import MongoClient, DESCENDING
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

app = Flask(__name__, static_folder="static")

# ── MongoDB Connection ──
MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

if not MONGO_URI:
    print("❌ MONGODB_URI not found in .env — copy .env.example and fill it in")
    sys.exit(1)

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client[MONGO_DB]
arrests = db["arrests"]
leads = db["leads"]

print(f"✅ Connected to MongoDB: {MONGO_DB}")


# ── Serve Frontend ──
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/<path:filename>")
def serve_file(filename):
    """Serve CSS, JS, PDF, and other static files from dashboard dir."""
    if filename.endswith((".css", ".js", ".png", ".ico", ".svg", ".pdf")):
        return send_from_directory(".", filename)
    return send_from_directory(".", "index.html")


# ── API: Compatibility endpoints (used by index.html) ──

@app.route("/api/status")
def api_status_compat():
    """Compatibility endpoint for index.html — returns scraper-style status from MongoDB."""
    try:
        pipeline = [
            {"$group": {
                "_id": "$county",
                "records": {"$sum": 1},
                "latest": {"$max": {"$ifNull": ["$updated_at", "$created_at"]}},
            }},
        ]
        results = list(arrests.aggregate(pipeline))
        scrapers = {}
        total_scraped = 0
        for r in results:
            county = r["_id"]
            count = r["records"]
            total_scraped += count
            # Estimate hot/warm from lead_score if available
            hot = arrests.count_documents({"county": county, "lead_score": {"$gte": 70}})
            warm = arrests.count_documents({"county": county, "lead_score": {"$gte": 40, "$lt": 70}})
            latest = r.get("latest")
            scrapers[county] = {
                "last_run": latest.isoformat() if isinstance(latest, datetime) else str(latest or ""),
                "records": count,
                "hot_leads": hot,
                "warm_leads": warm,
                "cold_leads": 0,
                "disqualified": 0,
                "duration_seconds": 0,
                "status": "ok",
                "error": None,
                "run_count": 1,
                "total_records": count,
            }
        return jsonify({
            "started_at": datetime.now(timezone.utc).isoformat(),
            "scrapers": scrapers,
            "total_scraped": total_scraped,
            "total_hot_leads": sum(s["hot_leads"] for s in scrapers.values()),
            "total_warm_leads": sum(s["warm_leads"] for s in scrapers.values()),
            "cycle_count": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mongo-stats")
def api_mongo_stats_compat():
    """Compatibility endpoint for index.html — MongoDB record stats."""
    try:
        total = arrests.count_documents({})
        county_pipeline = [
            {"$group": {"_id": "$county", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        by_county = {doc["_id"]: doc["count"] for doc in arrests.aggregate(county_pipeline)}
        hot = arrests.count_documents({"lead_score": {"$gte": 70}})
        warm = arrests.count_documents({"lead_score": {"$gte": 40, "$lt": 70}})
        cold = arrests.count_documents({"lead_score": {"$gte": 20, "$lt": 40}})
        disq = arrests.count_documents({"lead_score": {"$lt": 20}})

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_hot = list(arrests.find(
            {"lead_score": {"$gte": 70}, "scraped_at": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "bond_type": 1}
        ).sort("lead_score", -1).limit(20))

        return jsonify({
            "total_records": total,
            "by_county": by_county,
            "scores": {"hot": hot, "warm": warm, "cold": cold, "disqualified": disq},
            "recent_hot_leads": recent_hot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/command")
def api_command_center():
    """Rich command center data — actionable leads, bond-ready queue, revenue pipeline."""
    try:
        # Top bond-ready defendants: In Custody + High Bond + Hot/Warm score
        bond_ready = list(arrests.find(
            {"status": {"$regex": "custody|confined|held", "$options": "i"},
             "bond_amount": {"$gte": 1000}, "lead_score": {"$gte": 40}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "lead_status": 1,
             "status": 1, "booking_number": 1, "dob": 1, "arrest_date": 1,
             "booking_date": 1, "bond_type": 1, "detail_url": 1}
        ).sort("bond_amount", -1).limit(25))
        for doc in bond_ready:
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()

        # Revenue pipeline: sum of bondable amounts
        pipeline_total = sum(d.get("bond_amount", 0) for d in bond_ready)
        premium_est = sum(max(100, d.get("bond_amount", 0) * 0.1) for d in bond_ready)

        # Recent activity: last 10 arrests regardless of score
        recent = list(arrests.find(
            {}, {"_id": 0, "full_name": 1, "county": 1, "bond_amount": 1,
                 "lead_score": 1, "lead_status": 1, "scraped_at": 1,
                 "status": 1, "charges": 1}
        ).sort("scraped_at", -1).limit(10))
        for doc in recent:
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()

        # In-custody counts by county (for the map)
        custody_pipeline = [
            {"$match": {"status": {"$regex": "custody|confined|held", "$options": "i"}}},
            {"$group": {"_id": "$county", "count": {"$sum": 1},
                        "total_bond": {"$sum": "$bond_amount"}}},
            {"$sort": {"total_bond": -1}}
        ]
        custody_by_county = list(arrests.aggregate(custody_pipeline))

        return jsonify({
            "bond_ready": bond_ready,
            "pipeline_total": pipeline_total,
            "premium_estimate": premium_est,
            "bond_ready_count": len(bond_ready),
            "recent_activity": recent,
            "custody_by_county": [{"county": d["_id"], "count": d["count"],
                                   "total_bond": d.get("total_bond", 0)}
                                  for d in custody_by_county if d["_id"]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_leads_query():
    """Shared query builder for /api/leads and /api/leads/export."""
    query = {}

    # Status (lead_status: Hot / Warm / Cold / Disqualified)
    status_filter = request.args.get("status", "").strip()
    if status_filter:
        query["lead_status"] = status_filter

    # Multi-county support: county=Lee,Collier,Charlotte
    county_filter = request.args.get("county", "").strip()
    if county_filter:
        counties = [c.strip() for c in county_filter.split(",") if c.strip()]
        if len(counties) == 1:
            query["county"] = counties[0]
        elif len(counties) > 1:
            query["county"] = {"$in": counties}

    # Custody filter: true = In Custody only, false = Released only, empty = all
    custody_param = request.args.get("custody", "").strip().lower()
    if custody_param == "true":
        query["status"] = {"$regex": "custody|confined|held", "$options": "i"}
    elif custody_param == "released":
        query["status"] = {"$regex": "released|bonded|rts", "$options": "i"}

    # Date range: days=1..30 filters by scraped_at relative to today
    days_param = request.args.get("days", "").strip()
    if days_param:
        try:
            days_int = int(days_param)
            if 1 <= days_int <= 30:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days_int)
                # scraped_at stored as ISO string — compare as string
                query["scraped_at"] = {"$gte": cutoff.strftime("%Y-%m-%dT%H:%M:%S")}
        except (ValueError, TypeError):
            pass

    # Min bond
    min_bond = request.args.get("min_bond", "").strip()
    if min_bond:
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass

    # Search (name, charges, booking #, case #)
    search = request.args.get("search", "").strip()
    if search:
        pattern = re_mod.compile(re_mod.escape(search), re_mod.IGNORECASE)
        search_or = [
            {"full_name": {"$regex": pattern}},
            {"charges": {"$regex": pattern}},
            {"booking_number": {"$regex": pattern}},
            {"case_number": {"$regex": pattern}},
        ]
        # Merge with existing $or if date range set one
        if "$or" in query and query["$or"]:
            # Can't have two $or at top level; wrap in $and
            existing_or = query.pop("$or")
            query["$and"] = [{"$or": existing_or}, {"$or": search_or}]
        else:
            query.pop("$or", None)
            query["$or"] = search_or

    return query, status_filter, county_filter, search


@app.route("/api/leads")
def api_leads_compat():
    """Filterable, sortable leads list with multi-county and date range support."""
    try:
        query, status_filter, county_filter, search = _build_leads_query()

        sort_field = request.args.get("sort", "lead_score").strip()
        sort_order = -1 if request.args.get("order", "desc").strip() == "desc" else 1
        sort_map = {
            "lead_score": "lead_score", "bond_amount": "bond_amount",
            "booking_date": "booking_date", "full_name": "full_name",
            "county": "county", "arrest_date": "arrest_date",
            "created_at": "created_at",
        }
        mongo_sort = sort_map.get(sort_field, "lead_score")
        page = max(1, int(request.args.get("page", 1)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        skip = (page - 1) * limit
        projection = {
            "_id": 0, "full_name": 1, "first_name": 1, "last_name": 1,
            "booking_number": 1, "county": 1, "charges": 1, "bond_amount": 1,
            "bond_type": 1, "lead_score": 1, "lead_status": 1, "status": 1,
            "arrest_date": 1, "booking_date": 1, "court_date": 1,
            "court_location": 1, "case_number": 1, "dob": 1, "sex": 1,
            "race": 1, "address": 1, "detail_url": 1, "facility": 1,
            "mugshot_url": 1, "scraped_at": 1, "created_at": 1,
        }
        total_matching = arrests.count_documents(query)
        cursor = arrests.find(query, projection).sort(mongo_sort, sort_order).skip(skip).limit(limit)
        leads_list = []
        for doc in cursor:
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            leads_list.append(doc)
        counties_list = sorted(arrests.distinct("county"))
        return jsonify({
            "leads": leads_list,
            "total": total_matching,
            "page": page,
            "limit": limit,
            "pages": max(1, (total_matching + limit - 1) // limit),
            "counties": counties_list,
            "query": {
                "status": status_filter, "county": county_filter,
                "search": search, "sort": sort_field,
                "order": "desc" if sort_order == -1 else "asc",
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads/export")
def api_leads_export():
    """CSV export of current filtered leads."""
    try:
        query, _, _, _ = _build_leads_query()
        sort_field = request.args.get("sort", "lead_score").strip()
        sort_order = -1 if request.args.get("order", "desc").strip() == "desc" else 1
        sort_map = {
            "lead_score": "lead_score", "bond_amount": "bond_amount",
            "booking_date": "booking_date", "full_name": "full_name",
            "county": "county", "arrest_date": "arrest_date",
        }
        mongo_sort = sort_map.get(sort_field, "lead_score")
        cursor = arrests.find(query, {"_id": 0}).sort(mongo_sort, sort_order).limit(5000)

        columns = [
            "full_name", "county", "charges", "bond_amount", "bond_type",
            "lead_score", "lead_status", "status", "booking_number",
            "arrest_date", "booking_date", "court_date", "court_location",
            "case_number", "dob", "sex", "race", "address", "facility",
            "detail_url",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for doc in cursor:
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            writer.writerow(doc)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=shamrock_leads_{timestamp}.csv"},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads/<booking_number>")
def api_lead_detail_compat(booking_number):
    """Get full detail for a single lead by booking number."""
    try:
        doc = arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not doc:
            try:
                doc = arrests.find_one({"booking_number": int(booking_number)}, {"_id": 0})
            except (ValueError, TypeError):
                pass
        if not doc:
            return jsonify({"error": "Not found"}), 404
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        return jsonify(doc)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Overview Stats ──
@app.route("/api/stats")
def api_stats():
    """High-level dashboard stats."""
    total = arrests.count_documents({})
    counties = arrests.distinct("county")

    # Today's new arrests
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_count = arrests.count_documents({
        "created_at": {"$gte": today_start}
    })

    # Bond stats
    pipeline = [
        {"$match": {"bond_amount": {"$gt": 0}}},
        {"$group": {
            "_id": None,
            "avg_bond": {"$avg": "$bond_amount"},
            "max_bond": {"$max": "$bond_amount"},
            "total_bond": {"$sum": "$bond_amount"},
        }}
    ]
    bond_stats = list(arrests.aggregate(pipeline))
    bond = bond_stats[0] if bond_stats else {"avg_bond": 0, "max_bond": 0, "total_bond": 0}

    # High-value leads (bond > $2500)
    high_value = arrests.count_documents({"bond_amount": {"$gte": 2500}})

    return jsonify({
        "total_arrests": total,
        "counties_active": len(counties),
        "today_new": today_count,
        "avg_bond": round(bond.get("avg_bond", 0), 2),
        "max_bond": round(bond.get("max_bond", 0), 2),
        "total_bond_value": round(bond.get("total_bond", 0), 2),
        "high_value_leads": high_value,
    })


# ── API: County Breakdown ──
@app.route("/api/counties")
def api_counties():
    """Per-county stats."""
    pipeline = [
        {"$group": {
            "_id": "$county",
            "total": {"$sum": 1},
            "in_custody": {
                "$sum": {"$cond": [
                    {"$regexMatch": {"input": {"$ifNull": ["$status", ""]}, "regex": "custody|confined|held", "options": "i"}},
                    1, 0
                ]}
            },
            "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
            "max_bond": {"$max": "$bond_amount"},
            "total_bond": {"$sum": "$bond_amount"},
            "latest_scrape": {"$max": "$scrape_timestamp"},
        }},
        {"$sort": {"total": -1}},
    ]
    results = list(arrests.aggregate(pipeline))
    return jsonify([{
        "county": r["_id"],
        "total": r["total"],
        "in_custody": r["in_custody"],
        "avg_bond": round(r["avg_bond"] or 0, 2),
        "max_bond": round(r["max_bond"] or 0, 2),
        "total_bond": round(r["total_bond"] or 0, 2),
        "latest_scrape": r["latest_scrape"],
    } for r in results])


# ── API: Recent Arrests ──
@app.route("/api/arrests")
def api_arrests():
    """Paginated, filterable arrest list."""
    county = request.args.get("county", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    sort_by = request.args.get("sort", "booking_date")
    sort_dir = int(request.args.get("dir", -1))
    min_bond = request.args.get("min_bond", "")

    query = {}
    if county:
        query["county"] = county
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        query["bond_amount"] = {"$gte": float(min_bond)}

    total = arrests.count_documents(query)
    cursor = (
        arrests.find(query, {"_id": 0, "extra": 0})
        .sort(sort_by, sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    results = []
    for doc in cursor:
        # Convert datetime objects to strings
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "arrests": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


# ── API: Bond Distribution ──
@app.route("/api/bond-distribution")
def api_bond_distribution():
    """Bond amount distribution for charts."""
    pipeline = [
        {"$match": {"bond_amount": {"$gt": 0}}},
        {"$bucket": {
            "groupBy": "$bond_amount",
            "boundaries": [0, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 500000, 1000001],
            "default": "1000001+",
            "output": {"count": {"$sum": 1}, "avg": {"$avg": "$bond_amount"}}
        }}
    ]
    results = list(arrests.aggregate(pipeline))
    labels = ["$0-500", "$500-1K", "$1K-2.5K", "$2.5K-5K", "$5K-10K",
              "$10K-25K", "$25K-50K", "$50K-100K", "$100K-500K", "$500K+"]
    counts = [0] * len(labels)
    for r in results:
        idx_map = {0: 0, 500: 1, 1000: 2, 2500: 3, 5000: 4,
                   10000: 5, 25000: 6, 50000: 7, 100000: 8, 500000: 9}
        idx = idx_map.get(r["_id"], 9)
        counts[idx] = r["count"]

    return jsonify({"labels": labels, "counts": counts})


# ── API: Charge Frequency ──
@app.route("/api/top-charges")
def api_top_charges():
    """Most common charge keywords."""
    pipeline = [
        {"$match": {"charges": {"$exists": True, "$ne": ""}}},
        {"$project": {"words": {"$split": [{"$toUpper": "$charges"}, " | "]}}},
        {"$unwind": "$words"},
        {"$group": {"_id": "$words", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    results = list(arrests.aggregate(pipeline))
    return jsonify([{"charge": r["_id"], "count": r["count"]} for r in results])


# ── API: High-Value Targets (Bounty Board) ──
@app.route("/api/bounty-board")
def api_bounty_board():
    """High-value unposted bonds (>$2,500). Sortable by bond, date, county."""
    sort_by = request.args.get("sort", "bond_amount")
    sort_dir = int(request.args.get("dir", -1))
    county = request.args.get("county", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))

    query = {
        "bond_amount": {"$gte": 2500},
        "bond_paid": {"$nin": ["YES", "POSTED", "BONDED"]},
    }
    if county:
        query["county"] = county

    total = arrests.count_documents(query)
    cursor = (
        arrests.find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    results = []
    for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)
    return jsonify({
        "targets": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


# ── API: Scraping Activity Timeline ──
@app.route("/api/timeline")
def api_timeline():
    """Scraping activity over time (by date)."""
    pipeline = [
        {"$addFields": {
            "date_str": {
                "$cond": {
                    "if": {"$ne": [{"$type": "$created_at"}, "missing"]},
                    "then": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "else": "$booking_date"
                }
            }
        }},
        {"$group": {
            "_id": {"date": "$date_str", "county": "$county"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id.date": 1}},
    ]
    results = list(arrests.aggregate(pipeline))
    # Restructure for frontend
    dates = sorted(set(r["_id"]["date"] for r in results if r["_id"]["date"]))
    counties = sorted(set(r["_id"]["county"] for r in results if r["_id"]["county"]))
    series = {}
    for c in counties:
        series[c] = {d: 0 for d in dates}
    for r in results:
        d, c = r["_id"].get("date"), r["_id"].get("county")
        if d and c and c in series:
            series[c][d] = r["count"]

    return jsonify({
        "dates": dates,
        "series": {c: list(series[c].values()) for c in counties}
    })


# ── API: Scraper Health & Metrics ──
@app.route("/api/scraper-health")
def api_scraper_health():
    """Per-county scraper health metrics."""
    now = datetime.now(timezone.utc)
    h24_ago = now - timedelta(hours=24)

    pipeline = [
        {"$group": {
            "_id": "$county",
            "total_records": {"$sum": 1},
            "latest_record": {"$max": "$created_at"},
            "latest_booking": {"$max": "$booking_date"},
            "latest_scrape": {"$max": "$scrape_timestamp"},
            "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
            "max_bond": {"$max": "$bond_amount"},
            "total_bond": {"$sum": "$bond_amount"},
            "in_custody": {
                "$sum": {"$cond": [
                    {"$regexMatch": {"input": {"$ifNull": ["$status", ""]}, "regex": "custody|confined|held", "options": "i"}},
                    1, 0
                ]}
            },
        }},
        {"$sort": {"total_records": -1}},
    ]
    results = list(arrests.aggregate(pipeline))

    # Get 24h counts per county
    pipeline_24h = [
        {"$match": {"created_at": {"$gte": h24_ago}}},
        {"$group": {"_id": "$county", "count_24h": {"$sum": 1}}},
    ]
    counts_24h = {r["_id"]: r["count_24h"] for r in arrests.aggregate(pipeline_24h)}

    out = []
    for r in results:
        county = r["_id"]
        latest = r.get("latest_record") or r.get("latest_scrape")

        # Calculate staleness
        if isinstance(latest, datetime):
            hours_since = (now - latest).total_seconds() / 3600
        else:
            hours_since = 999

        if hours_since < 2:
            status = "healthy"
        elif hours_since < 6:
            status = "stale"
        elif hours_since < 24:
            status = "warning"
        else:
            status = "offline"

        out.append({
            "county": county,
            "total_records": r["total_records"],
            "in_custody": r["in_custody"],
            "records_24h": counts_24h.get(county, 0),
            "latest_record": latest.isoformat() if isinstance(latest, datetime) else str(latest or ""),
            "hours_since_update": round(hours_since, 1),
            "status": status,
            "avg_bond": round(r["avg_bond"] or 0, 2),
            "max_bond": round(r["max_bond"] or 0, 2),
            "total_bond": round(r["total_bond"] or 0, 2),
        })

    return jsonify(out)


# ── API: County-Specific Arrests (sorted by newest) ──
@app.route("/api/county-arrests/<county_name>")
def api_county_arrests(county_name):
    """Get arrests for a specific county, sorted newest first."""
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    sort_by = request.args.get("sort", "created_at")
    sort_dir = int(request.args.get("dir", -1))
    search = request.args.get("search", "")

    query = {"county": county_name}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
        ]

    total = arrests.count_documents(query)
    cursor = (
        arrests.find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    results = []
    for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "county": county_name,
        "arrests": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


# ── API: Defendant Profiles (paginated, full detail) ──
@app.route("/api/defendants")
def api_defendants():
    """Full defendant profiles with all booking sheet fields."""
    county = request.args.get("county", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    sort_by = request.args.get("sort", "bond_amount")
    sort_dir = int(request.args.get("dir", -1))
    min_bond = request.args.get("min_bond", "")

    query = {}
    if county:
        query["county"] = county
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
            {"case_number": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        query["bond_amount"] = {"$gte": float(min_bond)}

    total = arrests.count_documents(query)
    cursor = (
        arrests.find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    results = []
    for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "defendants": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


# ── API: Write Bond — Export to SignNow ──
@app.route("/api/write-bond", methods=["POST"])
def api_write_bond():
    """
    Accept defendant data + insurance company selection,
    format a GAS-compatible SignNow payload, and forward it.
    """
    import json as json_lib

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No payload received"}), 400

    insurer = data.get("insurance_company", "osi")
    defendant = data.get("defendant", {})
    booking = data.get("booking", {})
    bond = data.get("bond", {})
    charges = data.get("charges", "")
    court = data.get("court", {})

    # Validate required fields
    if not defendant.get("full_name"):
        return jsonify({"success": False, "error": "Defendant name required"}), 400
    if not booking.get("booking_number"):
        return jsonify({"success": False, "error": "Booking number required"}), 400

    # ── Format GAS-compatible payload ──
    # This matches the schema expected by SignNow_SendPaperwork.js
    gas_payload = {
        "action": "sendPaperwork",
        "source": "shamrock-leads-dashboard",
        "insuranceCompany": insurer.upper(),  # "OSI" or "PALMETTO"
        "defendant": {
            "fullName": defendant.get("full_name", ""),
            "firstName": defendant.get("first_name", ""),
            "lastName": defendant.get("last_name", ""),
            "middleName": defendant.get("middle_name", ""),
            "dob": defendant.get("dob", ""),
            "address": defendant.get("address", ""),
            "city": defendant.get("city", ""),
            "state": defendant.get("state", "FL"),
            "zip": defendant.get("zip", ""),
            "sex": defendant.get("sex", ""),
            "race": defendant.get("race", ""),
            "height": defendant.get("height", ""),
            "weight": defendant.get("weight", ""),
        },
        "booking": {
            "bookingNumber": booking.get("booking_number", ""),
            "county": booking.get("county", ""),
            "facility": booking.get("facility", ""),
            "agency": booking.get("agency", ""),
            "arrestDate": booking.get("arrest_date", ""),
            "bookingDate": booking.get("booking_date", ""),
        },
        "bond": {
            "totalAmount": bond.get("amount", 0),
            "premium": bond.get("premium", 0),
            "type": bond.get("type", ""),
            "paid": bond.get("paid", "NO"),
        },
        "charges": charges,
        "court": {
            "date": court.get("date", ""),
            "time": court.get("time", ""),
            "type": court.get("type", ""),
            "location": court.get("location", ""),
            "caseNumber": court.get("case_number", ""),
        },
    }

    # Log the formatted payload
    print(f"\n{'═' * 60}")
    print(f"📋 WRITE BOND — {defendant.get('full_name', 'Unknown')}")
    print(f"   Insurance: {insurer.upper()}")
    print(f"   Bond: ${bond.get('amount', 0):,.2f}")
    print(f"   Premium: ${bond.get('premium', 0):,.2f}")
    print(f"   County: {booking.get('county', 'Unknown')}")
    print(f"   Booking #: {booking.get('booking_number', 'N/A')}")
    print(f"{'═' * 60}")
    print(f"   GAS Payload: {json_lib.dumps(gas_payload, indent=2)[:500]}...")
    print(f"{'═' * 60}\n")

    # ── Forward to GAS (when configured) ──
    gas_url = os.getenv("GAS_WEB_APP_URL", "")
    if gas_url:
        try:
            import requests as req
            resp = req.post(gas_url, json=gas_payload, timeout=30)
            if resp.ok:
                return jsonify({
                    "success": True,
                    "message": f"Packet sent to GAS for {defendant.get('full_name')}",
                    "insurance_company": insurer.upper(),
                    "gas_response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:200],
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"GAS returned {resp.status_code}: {resp.text[:200]}",
                }), 502
        except Exception as e:
            return jsonify({
                "success": False,
                "error": f"GAS connection failed: {str(e)}",
            }), 502

    # No GAS URL configured — return success with payload for review
    return jsonify({
        "success": True,
        "message": f"Bond packet prepared for {defendant.get('full_name', 'Unknown')} via {insurer.upper()}",
        "insurance_company": insurer.upper(),
        "payload": gas_payload,
        "note": "GAS_WEB_APP_URL not configured — payload logged to console. Set GAS_WEB_APP_URL in .env to enable forwarding.",
    })

# ════════════════════════════════════════════════════════════════════════════════
# ACTIVE BONDS — GEOLOCATION & RISK MITIGATION
# ════════════════════════════════════════════════════════════════════════════════

active_bonds = db["active_bonds"]

# Ensure indexes on active_bonds collection
try:
    active_bonds.create_index("booking_number", unique=True, sparse=True)
    active_bonds.create_index("status")
    active_bonds.create_index("next_check_in_due")
    active_bonds.create_index("bond_date")
except Exception:
    pass


def _compute_risk_score(bond_doc: dict) -> int:
    """
    Compute a 0-100 risk score for an active bond.
    Higher = higher risk of FTA (failure to appear).
    """
    score = 50  # baseline

    # Missed check-ins increase risk
    missed = bond_doc.get("missed_check_ins", 0)
    score += min(missed * 10, 30)

    # Out-of-area pings increase risk
    out_of_area = bond_doc.get("out_of_area_count", 0)
    score += min(out_of_area * 8, 24)

    # High bond amount = higher risk
    bond_amount = float(bond_doc.get("bond_amount", 0) or 0)
    if bond_amount >= 50000:
        score += 10
    elif bond_amount >= 25000:
        score += 5

    # Violent/drug charges increase risk
    charges_raw = (bond_doc.get("charges_raw", "") or "").upper()
    high_risk_keywords = ["MURDER", "HOMICIDE", "ROBBERY", "TRAFFICKING", "ASSAULT",
                          "WEAPON", "FIREARM", "FLEE", "ESCAPE", "FUGITIVE"]
    for kw in high_risk_keywords:
        if kw in charges_raw:
            score += 5
            break

    # Recent location history reduces risk
    loc_history = bond_doc.get("location_history", [])
    if len(loc_history) >= 3:
        score -= 5

    return max(0, min(100, score))


@app.route("/api/active-bonds", methods=["GET"])
def api_active_bonds_list():
    """List all active bonds with risk scores and check-in status."""
    try:
        status_filter = request.args.get("status", "").strip()
        query = {}
        if status_filter:
            query["status"] = status_filter
        else:
            query["status"] = {"$in": ["active", "monitoring", "alert"]}

        bonds = list(active_bonds.find(query, {"_id": 0}).sort("bond_date", -1).limit(200))
        now = datetime.now(timezone.utc)

        for b in bonds:
            for k, v in b.items():
                if isinstance(v, datetime):
                    b[k] = v.isoformat()
            # Compute live risk score
            b["risk_score"] = _compute_risk_score(b)
            # Check if overdue
            next_due_str = b.get("next_check_in_due", "")
            if next_due_str:
                try:
                    from dateutil import parser as dateparser
                    next_due = dateparser.parse(next_due_str)
                    if next_due and next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=timezone.utc)
                    b["check_in_overdue"] = next_due < now if next_due else False
                    b["hours_overdue"] = round((now - next_due).total_seconds() / 3600, 1) if b["check_in_overdue"] else 0
                except Exception:
                    b["check_in_overdue"] = False
                    b["hours_overdue"] = 0
            else:
                b["check_in_overdue"] = False
                b["hours_overdue"] = 0

        # Summary stats
        total = len(bonds)
        alerts = sum(1 for b in bonds if b.get("check_in_overdue") or b.get("risk_score", 0) >= 75)
        high_risk = sum(1 for b in bonds if b.get("risk_score", 0) >= 75)

        return jsonify({
            "bonds": bonds,
            "total": total,
            "alerts": alerts,
            "high_risk": high_risk,
            "updated_at": now.isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/active-bonds", methods=["POST"])
def api_active_bonds_create():
    """Register a new active bond after Write Bond is clicked."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No payload"}), 400

        booking_number = data.get("booking_number", "")
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number required"}), 400

        now = datetime.now(timezone.utc)
        check_in_hours = int(data.get("check_in_interval_hours", 24))

        doc = {
            "booking_number": booking_number,
            "defendant_name": data.get("defendant_name", ""),
            "county": data.get("county", ""),
            "bond_amount": float(data.get("bond_amount", 0) or 0),
            "premium": float(data.get("premium", 0) or 0),
            "surety": data.get("surety", "osi").upper(),
            "charges": data.get("charges", []),
            "charges_raw": data.get("charges_raw", ""),
            "bond_date": data.get("bond_date", now.isoformat()),
            "status": "active",
            "risk_score": 50,
            "check_in_required": True,
            "check_in_interval_hours": check_in_hours,
            "last_check_in": None,
            "next_check_in_due": (now + timedelta(hours=check_in_hours)).isoformat(),
            "missed_check_ins": 0,
            "out_of_area_count": 0,
            "geolocation_enabled": True,
            "location_history": [],
            "alerts": [],
            "defendant_info": data.get("defendant_info", {}),
            "created_at": now,
            "updated_at": now,
        }
        doc["risk_score"] = _compute_risk_score(doc)

        # Upsert by booking_number
        result = active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )

        return jsonify({
            "success": True,
            "message": f"Active bond registered for {doc['defendant_name']}",
            "booking_number": booking_number,
            "risk_score": doc["risk_score"],
            "upserted": result.upserted_id is not None,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/active-bonds/<booking_number>/check-in", methods=["POST"])
def api_active_bond_check_in(booking_number):
    """Record a geolocation check-in for an active bond."""
    try:
        data = request.get_json() or {}
        now = datetime.now(timezone.utc)

        bond = active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        lat = data.get("lat")
        lng = data.get("lng")
        accuracy = data.get("accuracy", 0)
        source = data.get("source", "manual")  # manual | gps | ip

        location_entry = {
            "timestamp": now.isoformat(),
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": source,
            "county": data.get("county", ""),
            "address": data.get("address", ""),
        }

        check_in_hours = bond.get("check_in_interval_hours", 24)
        next_due = (now + timedelta(hours=check_in_hours)).isoformat()

        # Determine if out of area (simple county check)
        home_county = bond.get("county", "").lower()
        checkin_county = data.get("county", "").lower()
        out_of_area = bool(checkin_county and home_county and checkin_county != home_county)

        update = {
            "$set": {
                "last_check_in": now.isoformat(),
                "next_check_in_due": next_due,
                "updated_at": now,
            },
            "$push": {
                "location_history": {
                    "$each": [location_entry],
                    "$slice": -100,  # keep last 100 pings
                }
            },
        }
        if out_of_area:
            update["$inc"] = {"out_of_area_count": 1}
            alert = {
                "type": "out_of_area",
                "message": f"Check-in from {checkin_county.title()} (home: {home_county.title()})",
                "timestamp": now.isoformat(),
                "location": location_entry,
            }
            update["$push"]["alerts"] = alert

        active_bonds.update_one({"booking_number": booking_number}, update)

        # Recompute risk score
        updated = active_bonds.find_one({"booking_number": booking_number})
        new_risk = _compute_risk_score(updated) if updated else 50
        active_bonds.update_one({"booking_number": booking_number}, {"$set": {"risk_score": new_risk}})

        return jsonify({
            "success": True,
            "message": "Check-in recorded",
            "booking_number": booking_number,
            "next_check_in_due": next_due,
            "risk_score": new_risk,
            "out_of_area": out_of_area,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/active-bonds/<booking_number>/alert", methods=["POST"])
def api_active_bond_alert(booking_number):
    """Manually add an alert to an active bond (e.g. FTA, warrant issued)."""
    try:
        data = request.get_json() or {}
        now = datetime.now(timezone.utc)
        alert = {
            "type": data.get("type", "manual"),
            "message": data.get("message", "Manual alert"),
            "severity": data.get("severity", "medium"),  # low | medium | high | critical
            "timestamp": now.isoformat(),
        }
        result = active_bonds.update_one(
            {"booking_number": booking_number},
            {"$push": {"alerts": alert}, "$set": {"status": "alert", "updated_at": now}}
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        return jsonify({"success": True, "alert": alert})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/active-bonds/<booking_number>/status", methods=["PATCH"])
def api_active_bond_status(booking_number):
    """Update bond status (active | monitoring | alert | exonerated | forfeited)."""
    try:
        data = request.get_json() or {}
        new_status = data.get("status", "active")
        valid_statuses = ["active", "monitoring", "alert", "exonerated", "forfeited", "surrendered"]
        if new_status not in valid_statuses:
            return jsonify({"success": False, "error": f"Invalid status. Use: {valid_statuses}"}), 400
        now = datetime.now(timezone.utc)
        result = active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"status": new_status, "updated_at": now}}
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        return jsonify({"success": True, "status": new_status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/active-bonds/missed-checkins", methods=["POST"])
def api_active_bonds_process_missed():
    """Cron-style endpoint: scan for overdue check-ins and increment missed count."""
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        # Find active bonds where next_check_in_due is in the past
        overdue = list(active_bonds.find({
            "status": {"$in": ["active", "monitoring"]},
            "check_in_required": True,
            "next_check_in_due": {"$lt": now_iso},
        }))
        updated = 0
        for bond in overdue:
            booking_number = bond.get("booking_number", "")
            missed = bond.get("missed_check_ins", 0) + 1
            check_in_hours = bond.get("check_in_interval_hours", 24)
            next_due = (now + timedelta(hours=check_in_hours)).isoformat()
            alert = {
                "type": "missed_check_in",
                "message": f"Missed check-in #{missed} for {bond.get('defendant_name', 'Unknown')}",
                "severity": "high" if missed >= 2 else "medium",
                "timestamp": now.isoformat(),
            }
            new_status = "alert" if missed >= 2 else bond.get("status", "active")
            new_risk = _compute_risk_score({**bond, "missed_check_ins": missed})
            active_bonds.update_one(
                {"booking_number": booking_number},
                {
                    "$set": {
                        "missed_check_ins": missed,
                        "next_check_in_due": next_due,
                        "status": new_status,
                        "risk_score": new_risk,
                        "updated_at": now,
                    },
                    "$push": {"alerts": alert},
                }
            )
            updated += 1

        return jsonify({"success": True, "processed": updated, "timestamp": now_iso})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Appearance Bond PDF Generator ────────────────────────────────────────────────

@app.route("/api/appearance-bond-pdf")
def api_appearance_bond_pdf():
    """
    Generate a pre-populated blank Appearance Bond PDF.
    Query params: name, booking, county, bond, charge, surety, date, dob, address
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import io as _io

        name = request.args.get("name", "")
        booking = request.args.get("booking", "")
        county = request.args.get("county", "")
        bond_amount = request.args.get("bond", "0")
        charge = request.args.get("charge", "")
        surety = request.args.get("surety", "osi").upper()
        bond_date = request.args.get("date", datetime.now().strftime("%m/%d/%Y"))
        dob = request.args.get("dob", "")
        address = request.args.get("address", "")

        surety_full = "Ohio Security Insurance Company" if surety == "OSI" else "Palmetto Surety Corporation"
        surety_state = "Ohio" if surety == "OSI" else "South Carolina"

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter,
                                leftMargin=0.75*inch, rightMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('title', parent=styles['Heading1'],
                                     fontSize=14, alignment=TA_CENTER, spaceAfter=4)
        sub_style = ParagraphStyle('sub', parent=styles['Normal'],
                                   fontSize=10, alignment=TA_CENTER, spaceAfter=2)
        label_style = ParagraphStyle('label', parent=styles['Normal'],
                                     fontSize=9, textColor=colors.grey)
        value_style = ParagraphStyle('value', parent=styles['Normal'],
                                     fontSize=11, spaceBefore=2, spaceAfter=8)
        body_style = ParagraphStyle('body', parent=styles['Normal'],
                                    fontSize=9, leading=14, spaceAfter=6)
        sig_style = ParagraphStyle('sig', parent=styles['Normal'],
                                   fontSize=9, spaceAfter=2)

        story = []

        # Header
        story.append(Paragraph("APPEARANCE BOND", title_style))
        story.append(Paragraph(f"State of Florida — {county} County", sub_style))
        story.append(Paragraph(f"Surety: {surety_full} ({surety_state})", sub_style))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.black, spaceAfter=10))

        # Defendant Info Table
        def_data = [
            [Paragraph("<b>Defendant Full Name</b>", label_style),
             Paragraph("<b>Date of Birth</b>", label_style),
             Paragraph("<b>Booking Number</b>", label_style)],
            [Paragraph(name or "_" * 30, value_style),
             Paragraph(dob or "_" * 15, value_style),
             Paragraph(booking or "_" * 15, value_style)],
            [Paragraph("<b>Address</b>", label_style),
             Paragraph("<b>County</b>", label_style),
             Paragraph("<b>Bond Date</b>", label_style)],
            [Paragraph(address or "_" * 30, value_style),
             Paragraph(county or "_" * 15, value_style),
             Paragraph(bond_date or "_" * 15, value_style)],
        ]
        def_table = Table(def_data, colWidths=[3*inch, 2*inch, 2*inch])
        def_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
            ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#f0f0f0')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(def_table)
        story.append(Spacer(1, 10))

        # Charge Section
        story.append(Paragraph("<b>CRIMINAL CHARGE</b>", label_style))
        charge_data = [
            [Paragraph("<b>Charge Description</b>", label_style),
             Paragraph("<b>Bond Amount</b>", label_style)],
            [Paragraph(charge or "_" * 50, value_style),
             Paragraph(f"${float(bond_amount or 0):,.2f}", value_style)],
        ]
        charge_table = Table(charge_data, colWidths=[5*inch, 2*inch])
        charge_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(charge_table)
        story.append(Spacer(1, 12))

        # Bond Conditions
        story.append(Paragraph("<b>CONDITIONS OF BOND</b>", label_style))
        conditions = [
            "1. The above-named defendant shall appear before the court as required and shall not depart without leave of court.",
            "2. The defendant shall notify the surety of any change of address within 24 hours.",
            "3. The defendant shall not leave the State of Florida without prior written consent of the court and surety.",
            "4. The defendant shall report to the surety as directed and comply with all check-in requirements.",
            "5. The defendant shall not commit any criminal offense during the period of this bond.",
            "6. Failure to appear shall result in forfeiture of this bond and issuance of a warrant for arrest.",
        ]
        for cond in conditions:
            story.append(Paragraph(cond, body_style))
        story.append(Spacer(1, 12))

        # Surety Statement
        story.append(Paragraph(
            f"We, the undersigned, as surety, hereby acknowledge ourselves bound to the State of Florida "
            f"in the sum of <b>${float(bond_amount or 0):,.2f}</b> for the appearance of the above-named defendant "
            f"before the court at the time and place required, and at all subsequent times and places to which "
            f"the case may be continued, and to answer the charge of: <b>{charge or 'as charged'}</b>.",
            body_style
        ))
        story.append(Spacer(1, 20))

        # Signature Lines
        sig_data = [
            [Paragraph("_" * 35, sig_style), Paragraph("_" * 35, sig_style)],
            [Paragraph("Bail Bond Agent / Surety Representative", sig_style),
             Paragraph("Date", sig_style)],
            [Paragraph(" ", sig_style), Paragraph(" ", sig_style)],
            [Paragraph("_" * 35, sig_style), Paragraph("_" * 35, sig_style)],
            [Paragraph("License Number", sig_style), Paragraph("County", sig_style)],
        ]
        sig_table = Table(sig_data, colWidths=[3.5*inch, 3.5*inch])
        sig_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(sig_table)
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Paragraph(
            f"<i>Generated by ShamrockLeads Intelligence Platform — {bond_date} — {surety_full}</i>",
            ParagraphStyle('footer', parent=styles['Normal'], fontSize=7,
                           alignment=TA_CENTER, textColor=colors.grey)
        ))

        doc.build(story)
        buf.seek(0)

        safe_name = re_mod.sub(r'[^A-Za-z0-9_-]', '_', name or 'defendant')
        safe_charge = re_mod.sub(r'[^A-Za-z0-9_-]', '_', charge[:20] if charge else 'charge')
        filename = f"AppearanceBond_{safe_name}_{safe_charge}_{bond_date.replace('/', '-')}.pdf"

        return Response(
            buf.read(),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500


# ════════════════════════════════════════════════════════════════════════════════
# MAINTENANCE & HEALTH ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """Trigger manual data cleanup. Returns purge statistics."""
    try:
        # Import here to avoid circular deps in dashboard-only mode
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from maintenance.cleanup import run_cleanup
        result = run_cleanup()
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/db-health")
def api_db_health():
    """MongoDB Atlas storage health — monitors against 512MB M0 limit."""
    try:
        db_stats = db.command("dbStats")
        data_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_size_mb = round(db_stats.get("storageSize", 0) / (1024 * 1024), 2)
        index_size_mb = round(db_stats.get("indexSize", 0) / (1024 * 1024), 2)

        M0_LIMIT_MB = 512
        usage_pct = round(storage_size_mb / M0_LIMIT_MB * 100, 1)

        # Per-collection breakdown
        collections_info = []
        for coll_name in ["arrests", "leads", "ingestion_log"]:
            try:
                coll_stats = db.command("collStats", coll_name)
                collections_info.append({
                    "name": coll_name,
                    "documents": coll_stats.get("count", 0),
                    "data_size_mb": round(coll_stats.get("size", 0) / (1024 * 1024), 2),
                    "storage_size_mb": round(coll_stats.get("storageSize", 0) / (1024 * 1024), 2),
                    "index_size_mb": round(coll_stats.get("totalIndexSize", 0) / (1024 * 1024), 2),
                })
            except Exception:
                collections_info.append({"name": coll_name, "error": "not found"})

        status = "healthy"
        if usage_pct > 85:
            status = "critical"
        elif usage_pct > 70:
            status = "warning"

        return jsonify({
            "status": status,
            "limit_mb": M0_LIMIT_MB,
            "data_size_mb": data_size_mb,
            "storage_size_mb": storage_size_mb,
            "index_size_mb": index_size_mb,
            "usage_pct": usage_pct,
            "collections": collections_info,
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    print("\n🍀 ShamrockLeads Intelligence Dashboard")
    print("   http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)

