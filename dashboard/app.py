"""
ShamrockLeads — Local Intelligence Dashboard
Flask server with MongoDB API endpoints.

Run:  python dashboard/app.py
Then: open http://localhost:5050
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request
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
    """Serve CSS, JS, and other static files from dashboard dir."""
    if filename.endswith((".css", ".js", ".png", ".ico", ".svg")):
        return send_from_directory(".", filename)
    return send_from_directory(".", "index.html")


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


if __name__ == "__main__":
    print("\n🍀 ShamrockLeads Intelligence Dashboard")
    print("   http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)
