"""
ShamrockLeads — Local Intelligence Dashboard
Flask server with MongoDB API endpoints.

Run:  python dashboard/app.py
Then: open http://localhost:5050
"""
from __future__ import annotations

import csv
import io
import os
import re as re_mod
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests as http_requests

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
poa_inventory = db["poa_inventory"]
imessage_outreach = db["imessage_outreach"]
print(f"✅ Connected to MongoDB: {MONGO_DB}")

# ── BlueBubbles iMessage Config (Multi-Server) ──
# Each office Mac runs its own BlueBubbles instance tied to a phone number.
# Env vars: BLUEBUBBLES_URL_0178, BLUEBUBBLES_PASSWORD_0178, etc.
BB_SERVERS = {}
for _phone_suffix, _label, _email in [
    ("0178", "(239) 955-0178", "shamrockbailoffice@gmail.com"),
    ("0314", "(239) 955-0314", "brendanoneal99@gmail.com"),
]:
    _url = os.getenv(f"BLUEBUBBLES_URL_{_phone_suffix}", "").rstrip("/")
    _pw = os.getenv(f"BLUEBUBBLES_PASSWORD_{_phone_suffix}", "")
    # Also check legacy single-server env vars as fallback for first server
    if not _url and _phone_suffix == "0178":
        _url = os.getenv("BLUEBUBBLES_URL", "").rstrip("/")
        _pw = os.getenv("BLUEBUBBLES_PASSWORD", "")
    if _url:
        BB_SERVERS[f"239955{_phone_suffix}"] = {
            "url": _url, "password": _pw,
            "label": _label, "email": _email, "suffix": _phone_suffix,
        }

def _get_bb_server(from_number):
    """Look up the BlueBubbles server config for a given from_number."""
    # Try exact match first, then try matching by last 4 digits
    if from_number in BB_SERVERS:
        return BB_SERVERS[from_number]
    for key, srv in BB_SERVERS.items():
        if key.endswith(from_number[-4:]):
            return srv
    # Return first available server as fallback
    return next(iter(BB_SERVERS.values()), None)

# ── POA Inventory — Seed from receipt data if collection is empty ──────────────
# Based on actual inventory receipts dated 04/20/2026.
# OSI: 75 powers  |  Palmetto: 146 powers  |  Grand total: 221
_POA_RECEIPT_DATA = [
    # OSI — Receipt dated 04/20/2026, exp 31-Dec-26
    {"surety_id": "osi", "prefix": "OSI3",   "max_bond": 3_000,   "start": 20134295, "end": 20134324, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI6",   "max_bond": 6_000,   "start": 20132136, "end": 20132150, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI16",  "max_bond": 16_000,  "start": 20136624, "end": 20136639, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI51",  "max_bond": 51_000,  "start": 20127651, "end": 20127660, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI101", "max_bond": 101_000, "start": 20128283, "end": 20128284, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI251", "max_bond": 251_000, "start": 20129019, "end": 20129020, "exp": "2026-12-30"},
    # Palmetto — Package #192184, dated 04/20/2026
    {"surety_id": "palmetto", "prefix": "PSC5",   "max_bond": 5_000,   "start": 2644670, "end": 2644777, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC15",  "max_bond": 15_000,  "start": 2644778, "end": 2644790, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC25",  "max_bond": 25_000,  "start": 2644791, "end": 2644809, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC50",  "max_bond": 50_000,  "start": 2644810, "end": 2644813, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC75",  "max_bond": 75_000,  "start": 2644814, "end": 2644814, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC105", "max_bond": 105_000, "start": 2644815, "end": 2644815, "exp": None},
]

def _seed_poa_inventory():
    """Seed poa_inventory collection from receipt data if it's empty."""
    try:
        if poa_inventory.count_documents({}) > 0:
            return  # Already seeded
        docs = []
        for tier in _POA_RECEIPT_DATA:
            for serial in range(tier["start"], tier["end"] + 1):
                docs.append({
                    "poa_number": str(serial),
                    "poa_prefix": tier["prefix"],
                    "poa_full": f"{tier['prefix']} {serial}",
                    "surety_id": tier["surety_id"],
                    "max_bond_value": tier["max_bond"],
                    "status": "available",
                    "expiration": tier["exp"],
                    "book_number": "receipt_2026-04-20",
                    "assigned_to_agent": "Brendan",
                    "received_at": "2026-04-20T00:00:00Z",
                    "bond_case_id": None,
                    "used_at": None,
                    "voided_at": None,
                    "void_reason": None,
                    "reported_at": None,
                })
        if docs:
            poa_inventory.create_index("poa_number", unique=True)
            poa_inventory.create_index([("surety_id", 1), ("status", 1)])
            poa_inventory.create_index([("poa_prefix", 1), ("status", 1)])
            poa_inventory.insert_many(docs, ordered=False)
            print(f"✅ POA inventory seeded: {len(docs)} powers ({sum(1 for d in docs if d['surety_id']=='osi')} OSI + {sum(1 for d in docs if d['surety_id']=='palmetto')} Palmetto)")
    except Exception as e:
        print(f"⚠️  POA inventory seed warning: {e}")

try:
    _seed_poa_inventory()
except Exception:
    pass

# ── Master list of all registered scraper counties ──
# This ensures the dropdown always shows all counties even before data arrives.
REGISTERED_COUNTIES = sorted([
    "Alachua", "Bay", "Brevard", "Broward", "Charlotte", "Citrus", "Clay",
    "Collier", "Columbia", "DeSoto", "Dixie", "Duval", "Escambia", "Flagler",
    "Gadsden", "Glades", "Hardee", "Hendry", "Hernando", "Highlands",
    "Hillsborough", "Indian River", "Jackson", "Lake", "Lee", "Leon",
    "Manatee", "Martin", "Monroe", "Nassau", "Okaloosa", "Okeechobee",
    "Orange", "Osceola", "Palm Beach", "Pasco", "Pinellas", "Polk",
    "Putnam", "Santa Rosa", "Sarasota", "Seminole", "St. Johns", "St. Lucie",
    "Sumter", "Suwannee", "Taylor", "Volusia", "Walton",
])


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
        by_county = {c: 0 for c in REGISTERED_COUNTIES}  # Start with all registered
        for doc in arrests.aggregate(county_pipeline):
            by_county[doc["_id"]] = doc["count"]  # Override with actual counts
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
        # Bond-ready filter: In Custody + $1K+ Bond + Score >= 40
        bond_ready_filter = {
            "status": {"$regex": "custody|confined|held", "$options": "i"},
            "bond_amount": {"$gte": 1000},
            "lead_score": {"$gte": 40},
        }

        # TRUE count & pipeline totals (not capped by table limit)
        bond_ready_count = arrests.count_documents(bond_ready_filter)
        pipeline_agg = list(arrests.aggregate([
            {"$match": bond_ready_filter},
            {"$group": {
                "_id": None,
                "total_bond": {"$sum": "$bond_amount"},
                "count": {"$sum": 1},
            }}
        ]))
        pipeline_total = pipeline_agg[0]["total_bond"] if pipeline_agg else 0
        premium_est = max(pipeline_total * 0.1, bond_ready_count * 100) if bond_ready_count else 0

        # Top 25 for the table display
        bond_ready = list(arrests.find(
            bond_ready_filter,
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "lead_status": 1,
             "status": 1, "booking_number": 1, "dob": 1, "arrest_date": 1,
             "booking_date": 1, "bond_type": 1, "detail_url": 1}
        ).sort("bond_amount", -1).limit(25))
        for doc in bond_ready:
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()

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
            "bond_ready_count": bond_ready_count,
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
        db_counties = arrests.distinct("county")
        counties_list = sorted(set(REGISTERED_COUNTIES + [c for c in db_counties if c]))
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

        # Calculate staleness — normalize naive datetimes from MongoDB to UTC-aware
        if isinstance(latest, datetime):
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
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
# PROSPECTIVE BONDS — LEAD PIPELINE TRACKER
# ════════════════════════════════════════════════════════════════════════════════

prospective_bonds = db["prospective_bonds"]

# Ensure indexes
try:
    prospective_bonds.create_index("booking_number", unique=True, sparse=True)
    prospective_bonds.create_index("stage")
    prospective_bonds.create_index("status")
    prospective_bonds.create_index("created_at")
except Exception:
    pass


@app.route("/api/prospective-bonds", methods=["POST"])
def api_prospective_create():
    """Create a prospective bond from an arrest record.
    
    Snapshots the defendant data from the arrests collection and
    creates a pipeline record in the 'contacted' stage.
    """
    try:
        data = request.get_json(force=True)
        booking_number = (data.get("booking_number") or "").strip()
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number is required"}), 400

        # Check if already exists
        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if existing:
            return jsonify({
                "success": False,
                "error": "Already tracked as prospective bond",
                "stage": existing.get("stage", "contacted"),
            }), 409

        # Snapshot defendant data from arrests collection
        arrest_doc = arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not arrest_doc:
            # Allow creation even without arrest record (manual entry)
            arrest_doc = {}

        now = datetime.now(timezone.utc)
        doc = {
            "booking_number": booking_number,
            "defendant_name": data.get("defendant_name") or arrest_doc.get("full_name", "Unknown"),
            "county": data.get("county") or arrest_doc.get("county", ""),
            "bond_amount": float(data.get("bond_amount") or arrest_doc.get("bond_amount", 0) or 0),
            "charges": data.get("charges") or arrest_doc.get("charges", ""),
            "lead_score": int(data.get("lead_score") or arrest_doc.get("lead_score", 0) or 0),
            "lead_status": data.get("lead_status") or arrest_doc.get("lead_status", ""),
            "detail_url": arrest_doc.get("detail_url", ""),

            # Pipeline state
            "stage": "contacted",
            "status": "active",

            # Indemnitor / Cosigner (populated later)
            "indemnitor": {
                "name": data.get("indemnitor_name", ""),
                "phone": data.get("indemnitor_phone", ""),
                "email": data.get("indemnitor_email", ""),
                "relationship": data.get("indemnitor_relationship", ""),
            },

            # Communication & timeline
            "communication_log": [],
            "timeline": [{
                "timestamp": now.isoformat(),
                "event": "created",
                "detail": data.get("note") or "Marked as prospective bond from Defendants tab",
                "agent": data.get("agent", "Dashboard"),
            }],

            # Closure
            "outcome": None,
            "outcome_note": "",
            "closed_at": None,

            # Full arrest snapshot
            "defendant_snapshot": arrest_doc,

            # Metadata
            "created_at": now,
            "updated_at": now,
            "created_by": data.get("agent", "Dashboard"),
        }

        prospective_bonds.insert_one(doc)
        doc.pop("_id", None)
        # Convert datetime for JSON
        doc["created_at"] = doc["created_at"].isoformat()
        doc["updated_at"] = doc["updated_at"].isoformat()

        return jsonify({"success": True, "prospective_bond": doc})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/prospective-bonds", methods=["GET"])
def api_prospective_list():
    """List prospective bonds with optional stage/status filter."""
    try:
        stage = request.args.get("stage", "").strip()
        status = request.args.get("status", "active").strip()
        search = request.args.get("search", "").strip()

        query = {}
        if stage:
            query["stage"] = stage
        if status and status != "all":
            query["status"] = status

        if search:
            query["$or"] = [
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
                {"county": {"$regex": search, "$options": "i"}},
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
            ]

        bonds = list(prospective_bonds.find(query, {"_id": 0}).sort("updated_at", -1).limit(200))

        # Convert datetimes
        for b in bonds:
            for k, v in b.items():
                if isinstance(v, datetime):
                    b[k] = v.isoformat()

        # Stage counts for KPI
        stage_counts = {}
        for s in ["contacted", "negotiating", "paperwork", "ready"]:
            stage_counts[s] = prospective_bonds.count_documents({"stage": s, "status": "active"})

        total_active = sum(stage_counts.values())

        # Messages sent today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        msgs_today = 0
        for b in bonds:
            for msg in b.get("communication_log", []):
                try:
                    ts = msg.get("timestamp", "")
                    if ts and ts >= today_start.isoformat():
                        msgs_today += 1
                except Exception:
                    pass

        return jsonify({
            "bonds": bonds,
            "total": len(bonds),
            "total_active": total_active,
            "stage_counts": stage_counts,
            "messages_today": msgs_today,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prospective-bonds/<booking_number>/stage", methods=["PATCH"])
def api_prospective_update_stage(booking_number):
    """Update the pipeline stage. Requires a note explaining the transition."""
    try:
        data = request.get_json(force=True)
        new_stage = (data.get("stage") or "").strip().lower()
        note = (data.get("note") or "").strip()
        agent = data.get("agent", "Dashboard")

        valid_stages = ["contacted", "negotiating", "paperwork", "ready"]
        if new_stage not in valid_stages:
            return jsonify({"error": f"Invalid stage. Must be one of: {valid_stages}"}), 400

        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        old_stage = existing.get("stage", "contacted")
        now = datetime.now(timezone.utc)

        timeline_entry = {
            "timestamp": now.isoformat(),
            "event": "stage_change",
            "detail": f"Stage: {old_stage} → {new_stage}" + (f" — {note}" if note else ""),
            "agent": agent,
            "old_stage": old_stage,
            "new_stage": new_stage,
        }

        prospective_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"stage": new_stage, "updated_at": now},
                "$push": {"timeline": timeline_entry},
            },
        )

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "old_stage": old_stage,
            "new_stage": new_stage,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prospective-bonds/<booking_number>/note", methods=["POST"])
def api_prospective_add_note(booking_number):
    """Add a note or communication log entry to a prospective bond."""
    try:
        data = request.get_json(force=True)
        note_text = (data.get("note") or data.get("message") or "").strip()
        channel = (data.get("channel") or "note").strip()
        direction = (data.get("direction") or "note").strip()
        agent = data.get("agent", "Dashboard")

        if not note_text:
            return jsonify({"error": "note/message is required"}), 400

        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)

        # Add to communication_log if it's a message, otherwise timeline only
        updates = {"$set": {"updated_at": now}}

        if channel in ("imessage", "sms", "phone", "left_vm", "sent_text_to", "walk_in", "whatsapp"):
            comm_entry = {
                "timestamp": now.isoformat(),
                "direction": direction,
                "channel": channel,
                "message": note_text,
                "from_number": data.get("from_number", ""),
                "to_number": data.get("to_number", ""),
                "agent": agent,
                "bb_message_id": data.get("bb_message_id", ""),
            }
            updates["$push"] = {
                "communication_log": comm_entry,
                "timeline": {
                    "timestamp": now.isoformat(),
                    "event": f"{direction}_{channel}",
                    "detail": note_text[:200],
                    "agent": agent,
                },
            }
        else:
            updates["$push"] = {
                "timeline": {
                    "timestamp": now.isoformat(),
                    "event": "note",
                    "detail": note_text,
                    "agent": agent,
                },
            }

        prospective_bonds.update_one({"booking_number": booking_number}, updates)

        return jsonify({"success": True, "booking_number": booking_number})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Full indemnitor field whitelist (mirrors GAS indemnity agreement) ──
INDEMNITOR_FIELDS = [
    # Personal
    "name", "firstName", "middleName", "lastName", "relationship",
    "dob", "ssn", "dl", "dlState", "sex", "race", "height", "weight",
    # Contact
    "phone", "email", "callback_phone",
    # Address
    "address", "city", "state", "zip",
    # Employment
    "employer", "employerPhone", "employerAddress", "employerCity",
    "employerState", "supervisor", "supervisorPhone", "occupation",
    "monthlyIncome",
    # Spouse
    "spouseName", "spousePhone", "spouseEmployer", "spouseEmployerPhone",
    "spouseAddress", "spouseDob", "spouseRelationship",
    # References ("people who could get a message to you")
    "ref1Name", "ref1Phone", "ref1Address", "ref1Relationship",
    "ref2Name", "ref2Phone", "ref2Address", "ref2Relationship",
]

# ── 14-document packet: Shamrock (universal) + OSI + Palmetto ──
DOCUMENT_CHECKLIST = {
    "shamrock": [
        {"key": "indemnity_agreement", "label": "Indemnity Agreement"},
        {"key": "bail_bond_application", "label": "Bail Bond Application"},
        {"key": "receipt", "label": "Premium Receipt"},
        {"key": "notice_to_indemnitor", "label": "Notice to Indemnitor"},
        {"key": "privacy_notice", "label": "Privacy Notice"},
        {"key": "gps_consent", "label": "GPS Monitoring Consent"},
        {"key": "payment_plan", "label": "Payment Plan Agreement"},
        {"key": "collateral_receipt", "label": "Collateral Receipt"},
    ],
    "osi": [
        {"key": "osi_appearance_bond", "label": "OSI Appearance Bond"},
        {"key": "osi_power_of_attorney", "label": "OSI Power of Attorney"},
        {"key": "osi_agent_affidavit", "label": "OSI Agent Affidavit"},
    ],
    "palmetto": [
        {"key": "palmetto_appearance_bond", "label": "Palmetto Appearance Bond"},
        {"key": "palmetto_power_of_attorney", "label": "Palmetto Power of Attorney"},
        {"key": "palmetto_agent_affidavit", "label": "Palmetto Agent Affidavit"},
    ],
}


@app.route("/api/prospective-bonds/<booking_number>/indemnitor", methods=["PATCH"])
def api_prospective_update_indemnitor(booking_number):
    """Update the indemnitor/cosigner info on a prospective bond (full field set)."""
    try:
        data = request.get_json(force=True)
        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)
        indemnitor = existing.get("indemnitor", {})
        # Merge ALL incoming indemnitor fields
        for field in INDEMNITOR_FIELDS:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        prospective_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"indemnitor": indemnitor, "updated_at": now},
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "indemnitor_updated",
                    "detail": f"Indemnitor info updated: {indemnitor.get('name', '') or ' '.join(filter(None, [indemnitor.get('firstName',''), indemnitor.get('lastName','')]))}"[:200],
                    "agent": data.get("agent", "Dashboard"),
                }},
            },
        )

        return jsonify({"success": True, "indemnitor": indemnitor})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════
# INDEMNITOR MANAGEMENT — Unified tab across prospective + active bonds
# ════════════════════════════════════════════════════════════════════════════════

@app.route("/api/indemnitors", methods=["GET"])
def api_indemnitors_list():
    """List all indemnitors across prospective_bonds AND active_bonds."""
    try:
        search = request.args.get("search", "").strip()
        source_filter = request.args.get("source", "").strip()
        stage_filter = request.args.get("stage", "").strip()
        limit = min(int(request.args.get("limit", 100)), 500)

        results = []

        # ── Pull from prospective_bonds ──
        p_query = {"indemnitor": {"$exists": True}}
        if search:
            p_query["$or"] = [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                {"indemnitor.phone": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]
        if stage_filter:
            p_query["stage"] = stage_filter

        for doc in prospective_bonds.find(p_query).sort("updated_at", -1).limit(limit):
            ind = doc.get("indemnitor", {})
            # Skip empty indemnitors
            ind_name = ind.get("name") or " ".join(filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])) or ""
            if not ind_name and not ind.get("phone") and not ind.get("email"):
                continue
            results.append({
                "booking_number": doc.get("booking_number", ""),
                "defendant_name": doc.get("defendant_name", ""),
                "county": doc.get("county", ""),
                "bond_amount": doc.get("bond_amount", 0),
                "stage": doc.get("stage", ""),
                "status": doc.get("status", ""),
                "bond_type": "prospective",
                "indemnitor": ind,
                "indemnitor_name": ind_name,
                "indemnitor_phone": ind.get("phone", ""),
                "indemnitor_email": ind.get("email", ""),
                "indemnitor_relationship": ind.get("relationship", ""),
                "source": doc.get("source", "dashboard"),
                "documents": doc.get("documents", {}),
                "created_at": doc["created_at"].isoformat() if hasattr(doc.get("created_at"), "isoformat") else str(doc.get("created_at", "")),
                "updated_at": doc["updated_at"].isoformat() if hasattr(doc.get("updated_at"), "isoformat") else str(doc.get("updated_at", "")),
            })

        # ── Pull from active_bonds ──
        a_query = {"indemnitor": {"$exists": True}}
        if search:
            a_query["$or"] = [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]

        for doc in active_bonds.find(a_query).sort("created_at", -1).limit(limit):
            ind = doc.get("indemnitor", {})
            ind_name = ind.get("name") or " ".join(filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])) or ""
            if not ind_name and not ind.get("phone") and not ind.get("email"):
                continue
            # Avoid dups if same booking_number already from prospective
            if any(r["booking_number"] == doc.get("booking_number") for r in results):
                continue
            results.append({
                "booking_number": doc.get("booking_number", ""),
                "defendant_name": doc.get("defendant_name", ""),
                "county": doc.get("county", ""),
                "bond_amount": doc.get("bond_amount", 0),
                "stage": "bonded",
                "status": doc.get("status", "active"),
                "bond_type": "active",
                "indemnitor": ind,
                "indemnitor_name": ind_name,
                "indemnitor_phone": ind.get("phone", ""),
                "indemnitor_email": ind.get("email", ""),
                "indemnitor_relationship": ind.get("relationship", ""),
                "source": doc.get("source", "dashboard"),
                "documents": doc.get("documents", {}),
                "created_at": doc["created_at"].isoformat() if hasattr(doc.get("created_at"), "isoformat") else str(doc.get("created_at", "")),
                "updated_at": doc.get("updated_at", doc.get("created_at", "")),
            })

        # Sort by updated_at descending
        results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        return jsonify({
            "success": True,
            "indemnitors": results[:limit],
            "total": len(results),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indemnitors/<booking_number>", methods=["GET"])
def api_indemnitor_detail(booking_number):
    """Get full indemnitor profile for a booking number."""
    try:
        # Check prospective first, then active
        doc = prospective_bonds.find_one({"booking_number": booking_number})
        bond_type = "prospective"
        if not doc:
            doc = active_bonds.find_one({"booking_number": booking_number})
            bond_type = "active"
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        ind = doc.get("indemnitor", {})
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "defendant_name": doc.get("defendant_name", ""),
            "county": doc.get("county", ""),
            "bond_amount": doc.get("bond_amount", 0),
            "stage": doc.get("stage", ""),
            "charges": doc.get("charges", ""),
            "surety": doc.get("surety", "osi"),
            "indemnitor": ind,
            "documents": doc.get("documents", {}),
            "communication_log": doc.get("communication_log", []),
            "timeline": doc.get("timeline", []),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indemnitors/<booking_number>", methods=["PATCH"])
def api_indemnitor_update(booking_number):
    """Update full indemnitor profile (searches both collections)."""
    try:
        data = request.get_json(force=True)
        now = datetime.now(timezone.utc)

        # Try prospective first
        doc = prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        indemnitor = doc.get("indemnitor", {})
        for field in INDEMNITOR_FIELDS:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        # Build display name
        ind_name = indemnitor.get("name") or " ".join(
            filter(None, [indemnitor.get("firstName", ""), indemnitor.get("lastName", "")])
        )

        update_ops = {
            "$set": {"indemnitor": indemnitor, "updated_at": now},
        }
        # Only push timeline for prospective_bonds (active_bonds may not have it)
        if collection == prospective_bonds:
            update_ops["$push"] = {"timeline": {
                "timestamp": now.isoformat(),
                "event": "indemnitor_profile_updated",
                "detail": f"Full profile updated: {ind_name}"[:200],
                "agent": data.get("agent", "Dashboard"),
            }}

        collection.update_one({"booking_number": booking_number}, update_ops)
        return jsonify({"success": True, "indemnitor": indemnitor})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indemnitors/<booking_number>/documents", methods=["GET"])
def api_indemnitor_documents(booking_number):
    """Get document checklist for an indemnitor's bond."""
    try:
        doc = prospective_bonds.find_one({"booking_number": booking_number})
        bond_type = "prospective"
        if not doc:
            doc = active_bonds.find_one({"booking_number": booking_number})
            bond_type = "active"
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        saved_docs = doc.get("documents", {})
        surety = doc.get("surety", "osi")

        # Build checklist with saved status
        checklist = {}
        for section, items in DOCUMENT_CHECKLIST.items():
            checklist[section] = []
            for item in items:
                checklist[section].append({
                    **item,
                    "signed": saved_docs.get(item["key"], {}).get("signed", False),
                    "signed_at": saved_docs.get(item["key"], {}).get("signed_at", ""),
                    "signnow_id": saved_docs.get(item["key"], {}).get("signnow_id", ""),
                })

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "surety": surety,
            "checklist": checklist,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indemnitors/<booking_number>/documents", methods=["PATCH"])
def api_indemnitor_documents_update(booking_number):
    """Toggle document signed status."""
    try:
        data = request.get_json(force=True)
        doc_key = data.get("doc_key", "")
        signed = data.get("signed", False)

        if not doc_key:
            return jsonify({"error": "doc_key required"}), 400

        doc = prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        now = datetime.now(timezone.utc)
        collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                f"documents.{doc_key}.signed": signed,
                f"documents.{doc_key}.signed_at": now.isoformat() if signed else "",
                "updated_at": now,
            }},
        )

        return jsonify({"success": True, "doc_key": doc_key, "signed": signed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indemnitors/<booking_number>/payment-link", methods=["POST"])
def api_indemnitor_payment_link(booking_number):
    """Generate or return a SwipeSimple payment link for this bond."""
    try:
        doc = prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        ind = doc.get("indemnitor", {})
        ind_name = ind.get("name") or " ".join(
            filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
        ) or "Indemnitor"
        bond_amount = doc.get("bond_amount", 0)
        premium = round(float(bond_amount) * 0.10, 2) if bond_amount else 0

        # Build SwipeSimple payment link (configurable base URL)
        base_url = os.environ.get("SWIPESIMPLE_URL", "https://shamrockbailbonds.biz/payment")
        params = {
            "amount": str(premium),
            "name": ind_name,
            "booking": booking_number,
            "county": doc.get("county", ""),
        }
        from urllib.parse import urlencode
        payment_url = f"{base_url}?{urlencode(params)}"

        # Store payment link on the bond record
        now = datetime.now(timezone.utc)
        collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "payment_link": payment_url,
                "payment_premium": premium,
                "updated_at": now,
            }},
        )

        return jsonify({
            "success": True,
            "payment_link": payment_url,
            "premium": premium,
            "bond_amount": bond_amount,
            "indemnitor_name": ind_name,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prospective-bonds/<booking_number>/close", methods=["POST"])
def api_prospective_close(booking_number):
    """Close a prospective bond with an outcome reason."""
    try:
        data = request.get_json(force=True)
        outcome = (data.get("outcome") or "").strip()
        outcome_note = (data.get("note") or "").strip()
        agent = data.get("agent", "Dashboard")

        valid_outcomes = ["bonded", "lost_to_competitor", "released_ror", "no_contact",
                          "declined", "left_vm", "sent_text_to", "other"]
        if outcome not in valid_outcomes:
            return jsonify({"error": f"Invalid outcome. Must be one of: {valid_outcomes}"}), 400

        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)
        prospective_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": "closed",
                    "outcome": outcome,
                    "outcome_note": outcome_note,
                    "closed_at": now.isoformat(),
                    "updated_at": now,
                },
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "closed",
                    "detail": f"Closed: {outcome}" + (f" — {outcome_note}" if outcome_note else ""),
                    "agent": agent,
                }},
            },
        )

        return jsonify({"success": True, "outcome": outcome})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prospective-bonds/<booking_number>/officialize", methods=["POST"])
def api_prospective_officialize(booking_number):
    """Promote a prospective bond to an active bond.
    
    This marks the prospective record as 'promoted' and returns the
    defendant + indemnitor data needed to pre-fill the Write Bond modal.
    The actual active bond creation happens via the existing /api/write-bond flow.
    """
    try:
        existing = prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        if existing.get("status") == "promoted":
            return jsonify({"error": "Already promoted to active bond"}), 409

        now = datetime.now(timezone.utc)
        prospective_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": "promoted",
                    "outcome": "bonded",
                    "closed_at": now.isoformat(),
                    "updated_at": now,
                },
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "promoted",
                    "detail": "Promoted to Active Bond — bond officialized",
                    "agent": "Dashboard",
                }},
            },
        )

        # Return data needed for Write Bond modal pre-fill
        snapshot = existing.get("defendant_snapshot", {})
        indemnitor = existing.get("indemnitor", {})
        return jsonify({
            "success": True,
            "defendant": {
                "full_name": existing.get("defendant_name", ""),
                "booking_number": booking_number,
                "county": existing.get("county", ""),
                "bond_amount": existing.get("bond_amount", 0),
                "charges": existing.get("charges", ""),
                "dob": snapshot.get("dob", ""),
                "address": snapshot.get("address", ""),
                **{k: snapshot.get(k, "") for k in [
                    "first_name", "last_name", "sex", "race", "height", "weight",
                    "facility", "arrest_date", "booking_date", "bond_type",
                    "court_date", "court_location", "case_number",
                ]},
            },
            "indemnitor": indemnitor,
            "communication_log": existing.get("communication_log", []),
            "timeline": existing.get("timeline", []),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    """Register a new active bond with POA assignment.
    
    Accepts poa_numbers array — each entry: {poa_full, poa_number, poa_prefix, charge, case_number}
    Auto-marks POAs as 'assigned' in poa_inventory collection.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No payload"}), 400

        booking_number = data.get("booking_number", "")
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number required"}), 400

        now = datetime.now(timezone.utc)
        check_in_hours = int(data.get("check_in_interval_hours", 24))

        # POA numbers with per-charge/case linkage
        poa_numbers = data.get("poa_numbers", [])
        surety_id = (data.get("surety", "osi") or "osi").lower()

        doc = {
            "booking_number": booking_number,
            "defendant_name": data.get("defendant_name", ""),
            "county": data.get("county", ""),
            "bond_amount": float(data.get("bond_amount", 0) or 0),
            "premium": float(data.get("premium", 0) or 0),
            "surety": surety_id.upper(),
            "poa_numbers": poa_numbers,
            "case_number": data.get("case_number", ""),
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

        # Auto-assign POA numbers in inventory (mark as used)
        assigned_poas = []
        for poa in poa_numbers:
            pn = str(poa.get("poa_number", "")).strip()
            if not pn:
                continue
            poa_result = poa_inventory.update_one(
                {"poa_number": pn, "surety_id": surety_id, "status": "available"},
                {"$set": {
                    "status": "assigned",
                    "bond_case_id": booking_number,
                    "used_at": now.isoformat(),
                }},
            )
            if poa_result.modified_count > 0:
                assigned_poas.append(pn)

        return jsonify({
            "success": True,
            "message": f"Active bond registered for {doc['defendant_name']}",
            "booking_number": booking_number,
            "risk_score": doc["risk_score"],
            "upserted": result.upserted_id is not None,
            "poas_assigned": assigned_poas,
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


# ── POA Inventory API ────────────────────────────────────────────────────────────

def _get_poa_tier_for_bond(surety_id: str, bond_amount: float) -> str:
    """
    Return the smallest POA prefix that covers the bond amount for the given surety.
    OSI tiers:     OSI3→$3k, OSI6→$6k, OSI16→$16k, OSI51→$51k, OSI101→$101k, OSI251→$251k
    Palmetto tiers: PSC5→$5k, PSC15→$15k, PSC25→$25k, PSC50→$50k, PSC75→$75k, PSC105→$105k
    """
    tiers = {
        "osi":     [(3000, "OSI3"), (6000, "OSI6"), (16000, "OSI16"),
                    (51000, "OSI51"), (101000, "OSI101"), (251000, "OSI251")],
        "palmetto": [(5000, "PSC5"), (15000, "PSC15"), (25000, "PSC25"),
                     (50000, "PSC50"), (75000, "PSC75"), (105000, "PSC105")],
    }
    for cap, prefix in tiers.get(surety_id.lower(), []):
        if bond_amount <= cap:
            return prefix
    # Bond exceeds all tiers — return highest available
    return tiers.get(surety_id.lower(), [(0, "UNKNOWN")])[-1][1]


@app.route("/api/poa/next", methods=["GET"])
def api_poa_next():
    """
    Suggest the next available POA number(s) for a given surety + bond amount.
    Query params:
      surety      — "osi" or "palmetto" (required)
      bond_amount — numeric bond value (required)
      count       — how many POAs to suggest (default 1, use for multi-charge)
    Returns:
      { surety, prefix, available, suggested: [{poa_full, poa_number, poa_prefix}] }
    """
    surety = (request.args.get("surety") or "").lower().strip()
    if surety not in ("osi", "palmetto"):
        return jsonify({"error": "surety must be 'osi' or 'palmetto'"}), 400
    try:
        bond_amount = float(request.args.get("bond_amount", 0) or 0)
    except ValueError:
        bond_amount = 0.0
    count = max(1, int(request.args.get("count", 1) or 1))

    prefix = _get_poa_tier_for_bond(surety, bond_amount)

    # Find next `count` available POAs in this prefix, ordered by serial number
    available_cursor = poa_inventory.find(
        {"surety_id": surety, "poa_prefix": prefix, "status": "available"},
        {"poa_number": 1, "poa_prefix": 1, "poa_full": 1, "_id": 0}
    ).sort("poa_number", 1).limit(count)
    suggested = list(available_cursor)

    total_available = poa_inventory.count_documents(
        {"surety_id": surety, "poa_prefix": prefix, "status": "available"}
    )
    total_surety = poa_inventory.count_documents(
        {"surety_id": surety, "status": "available"}
    )

    return jsonify({
        "surety": surety,
        "prefix": prefix,
        "bond_amount": bond_amount,
        "available_in_tier": total_available,
        "available_total": total_surety,
        "suggested": suggested,
        "warning": ("Low inventory in this tier" if total_available <= 3 else None),
    })


@app.route("/api/poa/assign", methods=["POST"])
def api_poa_assign():
    """
    Mark a POA as assigned to a bond case.
    Body: { poa_number, poa_prefix, surety_id, bond_case_id, booking_number }
    Returns: { success, poa_full, remaining_in_tier }
    """
    body = request.get_json(force=True) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = body.get("bond_case_id") or body.get("booking_number", "")

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id are required"}), 400

    # Find the POA
    doc = poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found for surety {surety_id}"}), 404
    if doc.get("status") != "available":
        return jsonify({"error": f"POA {poa_number} is already {doc.get('status')} — cannot assign"}), 409

    # Mark as assigned
    poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "assigned",
            "bond_case_id": str(bond_case_id),
            "used_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    remaining = poa_inventory.count_documents(
        {"surety_id": surety_id, "poa_prefix": doc.get("poa_prefix", poa_prefix), "status": "available"}
    )

    return jsonify({
        "success": True,
        "poa_number": poa_number,
        "poa_prefix": doc.get("poa_prefix", poa_prefix),
        "poa_full": doc.get("poa_full", f"{poa_prefix} {poa_number}"),
        "surety_id": surety_id,
        "bond_case_id": str(bond_case_id),
        "remaining_in_tier": remaining,
    })


@app.route("/api/poa/inventory", methods=["GET"])
def api_poa_inventory():
    """
    Return a summary of available POA inventory by surety and tier.
    Optional query param: surety=osi|palmetto
    """
    surety_filter = (request.args.get("surety") or "").lower().strip()
    match = {"status": "available"}
    if surety_filter in ("osi", "palmetto"):
        match["surety_id"] = surety_filter

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix", "max_bond_value": "$max_bond_value"},
            "available": {"$sum": 1},
            "next_serial": {"$min": "$poa_number"},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.max_bond_value": 1}},
    ]
    rows = list(poa_inventory.aggregate(pipeline))
    result = []
    for r in rows:
        result.append({
            "surety_id": r["_id"]["surety_id"],
            "poa_prefix": r["_id"]["poa_prefix"],
            "max_bond_value": r["_id"]["max_bond_value"],
            "available": r["available"],
            "next_serial": r["next_serial"],
            "next_poa_full": f"{r['_id']['poa_prefix']} {r['next_serial']}",
        })

    totals = {
        "osi": sum(r["available"] for r in result if r["surety_id"] == "osi"),
        "palmetto": sum(r["available"] for r in result if r["surety_id"] == "palmetto"),
    }
    return jsonify({"tiers": result, "totals": totals})


# ── POA Detail CRUD ──────────────────────────────────────────────────────────────

@app.route("/api/poa/list", methods=["GET"])
def api_poa_list():
    """
    Paginated list of all POA powers with filtering.
    Query params: page, limit, surety, status, search
    """
    page = max(1, int(request.args.get("page", 1) or 1))
    limit = min(200, max(1, int(request.args.get("limit", 50) or 50)))
    surety = (request.args.get("surety") or "").lower().strip()
    status = (request.args.get("status") or "").lower().strip()
    search = (request.args.get("search") or "").strip()

    match = {}
    if surety in ("osi", "palmetto"):
        match["surety_id"] = surety
    if status in ("available", "assigned", "voided"):
        match["status"] = status
    if search:
        match["$or"] = [
            {"poa_number": {"$regex": search, "$options": "i"}},
            {"poa_full": {"$regex": search, "$options": "i"}},
            {"bond_case_id": {"$regex": search, "$options": "i"}},
        ]

    total = poa_inventory.count_documents(match)
    pages = max(1, -(-total // limit))
    skip = (page - 1) * limit

    powers = list(poa_inventory.find(
        match,
        {"_id": 0}
    ).sort([("surety_id", 1), ("poa_prefix", 1), ("poa_number", 1)]).skip(skip).limit(limit))

    return jsonify({"powers": powers, "total": total, "page": page, "pages": pages, "limit": limit})


@app.route("/api/poa/add", methods=["POST"])
def api_poa_add():
    """
    Add new POA powers to inventory (range or single).
    Body: { surety_id, poa_prefix, start, end, max_bond_value, expiration }
    """
    body = request.get_json(force=True) or {}
    surety_id = str(body.get("surety_id", "")).lower().strip()
    prefix = str(body.get("poa_prefix", "")).strip()
    start = str(body.get("start", "")).strip()
    end = str(body.get("end", start)).strip()
    max_bond = float(body.get("max_bond_value", 0) or 0)
    expiration = body.get("expiration")

    if not surety_id or surety_id not in ("osi", "palmetto"):
        return jsonify({"error": "surety_id must be 'osi' or 'palmetto'"}), 400
    if not prefix or not start:
        return jsonify({"error": "poa_prefix and start are required"}), 400

    # Handle numeric range
    try:
        start_num = int(start)
        end_num = int(end)
    except ValueError:
        return jsonify({"error": "start and end must be numeric"}), 400

    if end_num < start_num:
        start_num, end_num = end_num, start_num

    count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for num in range(start_num, end_num + 1):
        poa_number = str(num)
        poa_full = f"{prefix} {poa_number}"
        # Skip if already exists
        if poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id, "poa_prefix": prefix}):
            continue
        poa_inventory.insert_one({
            "poa_number": poa_number,
            "poa_prefix": prefix,
            "poa_full": poa_full,
            "surety_id": surety_id,
            "max_bond_value": max_bond,
            "status": "available",
            "bond_case_id": None,
            "used_at": None,
            "voided_at": None,
            "expiration": expiration,
            "created_at": now_iso,
        })
        count += 1

    return jsonify({"success": True, "count": count, "surety_id": surety_id, "prefix": prefix})


@app.route("/api/poa/void", methods=["POST"])
def api_poa_void():
    """Mark a POA as voided (unusable)."""
    body = request.get_json(force=True) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    reason = body.get("reason", "")

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404

    poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": reason,
        }}
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} voided"})


@app.route("/api/poa/restore", methods=["POST"])
def api_poa_restore():
    """Restore a voided POA back to available."""
    body = request.get_json(force=True) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404
    if doc.get("status") != "voided":
        return jsonify({"error": f"POA {poa_number} is {doc.get('status')}, not voided"}), 409

    poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "voided_at": None, "void_reason": None, "bond_case_id": None}}
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} restored to available"})


@app.route("/api/poa/release", methods=["POST"])
def api_poa_release():
    """Release an assigned POA back to available status."""
    body = request.get_json(force=True) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404
    if doc.get("status") != "assigned":
        return jsonify({"error": f"POA {poa_number} is {doc.get('status')}, not assigned"}), 409

    poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None}}
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} released back to available"})


@app.route("/api/poa/reassign", methods=["POST"])
def api_poa_reassign():
    """Reassign an assigned POA to a different bond case."""
    body = request.get_json(force=True) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    new_booking = str(body.get("new_booking_number", "")).strip()

    if not poa_number or not surety_id or not new_booking:
        return jsonify({"error": "poa_number, surety_id, and new_booking_number required"}), 400

    doc = poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404
    if doc.get("status") != "assigned":
        return jsonify({"error": f"POA {poa_number} is {doc.get('status')}, not assigned"}), 409

    old_case = doc.get("bond_case_id", "")
    poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"bond_case_id": new_booking, "used_at": datetime.now(timezone.utc).isoformat()}}
    )
    return jsonify({
        "success": True, "poa_number": poa_number,
        "message": f"POA {poa_number} reassigned from {old_case} to {new_booking}",
        "old_case": old_case, "new_case": new_booking,
    })


# ── POA Image Upload (OCR Extraction) ───────────────────────────────────────────

@app.route("/api/poa/upload-image", methods=["POST"])
def api_poa_upload_image():
    """
    Upload an image of a POA power sheet. Extracts serial numbers via OCR.
    Returns list of extracted serial numbers for user confirmation before adding.
    """
    import re
    import tempfile

    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']
    surety_id = request.form.get('surety_id', 'osi').lower().strip()

    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Save to temp file
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in ('.jpg', '.jpeg', '.png', '.pdf', '.webp'):
        return jsonify({"error": f"Unsupported file type: {suffix}. Use JPG, PNG, or PDF."}), 400

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        extracted = []

        # Try pytesseract if available (preferred OCR path)
        try:
            from PIL import Image
            import pytesseract
            img = Image.open(tmp_path)
            text = pytesseract.image_to_string(img)
            # Extract serial numbers — patterns like: 6-8 digit numbers, possibly with prefix letters
            matches = re.findall(r'\b(\d{5,8})\b', text)
            extracted = list(dict.fromkeys(matches))  # dedupe while preserving order
        except ImportError:
            # Fallback: extract from filename if it contains serial patterns
            fname = os.path.splitext(file.filename)[0]
            matches = re.findall(r'(\d{5,8})', fname)
            extracted = list(dict.fromkeys(matches))
            if not extracted:
                return jsonify({
                    "error": "OCR not available on server (install pytesseract + tesseract-ocr). Use manual entry.",
                    "extracted": [],
                    "ocr_available": False,
                }), 200

        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        return jsonify({
            "success": True,
            "extracted": extracted[:100],  # cap at 100
            "count": len(extracted),
            "surety_id": surety_id,
            "ocr_available": True,
        })
    except Exception as e:
        return jsonify({"error": f"Processing failed: {str(e)}"}), 500


# ── Bond Finalization (Two-Step) ─────────────────────────────────────────────────

@app.route("/api/finalize-bond/step1/<booking_number>", methods=["POST"])
def api_finalize_bond_step1(booking_number):
    """
    Step 1: Validate data and return a review summary.
    Body: { insurance_company, poa_number, indemnitor_name, indemnitor_phone, agent, notes }
    """
    try:
        body = request.get_json(force=True) or {}

        # Find the defendant record
        defendant = db["arrests"].find_one({"booking_number": booking_number})
        if not defendant:
            defendant = db["prospective_bonds"].find_one({"booking_number": booking_number})
        if not defendant:
            return jsonify({"success": False, "error": "Defendant not found"}), 404

        # Get existing notes
        notes_doc = db.get_collection("defendant_notes").find_one({"booking_number": booking_number}) or {}

        # Build review object
        import uuid
        review_token = str(uuid.uuid4())[:8]

        insurance = body.get("insurance_company", "osi")
        insurance_names = {
            "osi": "O'Shaughnahill Surety & Insurance (OSI)",
            "palmetto": "Palmetto Surety Corporation",
            "accredited": "Accredited Surety", "allegheny": "Allegheny Casualty",
            "bankers": "Bankers Insurance", "other": "Other",
        }

        bond_amount = float(defendant.get("bond_amount", 0) or 0)
        charges = defendant.get("charges", "") or defendant.get("charges_raw", "")
        if isinstance(charges, list):
            charges = "; ".join(charges)

        review = {
            "booking_number": booking_number,
            "defendant_name": defendant.get("defendant_name", defendant.get("name", "Unknown")),
            "county": defendant.get("county", ""),
            "bond_amount": bond_amount,
            "premium": round(bond_amount * 0.10),
            "insurance_company": insurance_names.get(insurance, insurance),
            "surety_id": insurance,
            "poa_number": body.get("poa_number", notes_doc.get("poa_number", "")),
            "indemnitor_name": body.get("indemnitor_name", notes_doc.get("indemnitor_name", "")),
            "indemnitor_phone": body.get("indemnitor_phone", notes_doc.get("indemnitor_phone", "")),
            "court_date": defendant.get("court_date", ""),
            "case_number": defendant.get("case_number", ""),
            "charges": charges,
            "agent": body.get("agent", ""),
            "notes": body.get("notes", ""),
            "review_token": review_token,
        }

        return jsonify({"success": True, "review": review})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/finalize-bond/step2/<booking_number>", methods=["POST"])
def api_finalize_bond_step2(booking_number):
    """
    Step 2: Confirm and post bond to Active Bonds collection.
    Body: { review_token, confirmed_by, poa_number, notes }
    """
    try:
        body = request.get_json(force=True) or {}
        now = datetime.now(timezone.utc)

        # Find the defendant record
        defendant = db["arrests"].find_one({"booking_number": booking_number})
        if not defendant:
            defendant = db["prospective_bonds"].find_one({"booking_number": booking_number})
        if not defendant:
            return jsonify({"success": False, "error": "Defendant not found"}), 404

        notes_doc = db.get_collection("defendant_notes").find_one({"booking_number": booking_number}) or {}
        bond_amount = float(defendant.get("bond_amount", 0) or 0)
        charges = defendant.get("charges", "") or defendant.get("charges_raw", "")
        if isinstance(charges, list):
            charges_list = charges
            charges = "; ".join(charges)
        else:
            charges_list = [c.strip() for c in charges.split(";") if c.strip()] if charges else []

        surety_id = notes_doc.get("surety_id", "osi")
        poa_number = body.get("poa_number", notes_doc.get("poa_number", ""))

        # Create active bond record
        active_bond = {
            "booking_number": booking_number,
            "defendant_name": defendant.get("defendant_name", defendant.get("name", "Unknown")),
            "county": defendant.get("county", ""),
            "bond_amount": bond_amount,
            "premium": round(bond_amount * 0.10),
            "surety": surety_id,
            "poa_number": poa_number,
            "case_number": defendant.get("case_number", ""),
            "charges": charges_list,
            "charges_raw": charges,
            "court_date": defendant.get("court_date", ""),
            "indemnitor_name": notes_doc.get("indemnitor_name", ""),
            "indemnitor_phone": notes_doc.get("indemnitor_phone", ""),
            "agent": body.get("confirmed_by", ""),
            "status": "active",
            "risk_score": 50,
            "check_in_required": True,
            "check_in_interval_hours": 24,
            "missed_check_ins": 0,
            "location_history": [],
            "alerts": [],
            "bonded_at": now.isoformat(),
            "next_check_in_due": (now + timedelta(hours=24)).isoformat(),
            "created_at": now,
            "updated_at": now,
        }

        # Upsert into active_bonds
        active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": active_bond},
            upsert=True
        )

        # Mark POA as assigned if provided
        if poa_number:
            poa_inventory.update_one(
                {"poa_number": poa_number, "surety_id": surety_id, "status": "available"},
                {"$set": {
                    "status": "assigned",
                    "bond_case_id": booking_number,
                    "used_at": now.isoformat(),
                }}
            )

        # Update defendant notes to reflect bonded status
        db.get_collection("defendant_notes").update_one(
            {"booking_number": booking_number},
            {"$set": {
                "shamrock_status": "bonded",
                "bond_finalized": True,
                "finalized_at": now.isoformat(),
                "finalized_by": body.get("confirmed_by", ""),
                "poa_number": poa_number,
                "surety_id": surety_id,
            }},
            upsert=True
        )

        # Update prospective bond status if exists
        db["prospective_bonds"].update_one(
            {"booking_number": booking_number},
            {"$set": {"stage": "bonded", "updated_at": now.isoformat()}}
        )

        return jsonify({"success": True, "booking_number": booking_number, "status": "bonded"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── Appearance Bond PDF Generator ────────────────────────────────────────────────

@app.route("/health")
def health_check():
    """Docker/Hetzner healthcheck endpoint."""
    return jsonify({"status": "ok", "service": "shamrock-dashboard"})


@app.route("/api/appearance-bond-pdf", methods=["GET", "POST"])
def api_appearance_bond_pdf():
    """
    Generate a pre-populated Appearance Bond PDF using the official
    OSI or Palmetto surety-approved templates.

    Accepts GET query params or POST JSON body:
        name, booking, county, bond, charge, surety, date, dob, address,
        court_date, court_time, case_number, poa_number, court_type,
        first_name, last_name, indemnitor_name
    """
    try:
        try:
            from bond_pdf_service import generate_appearance_bond, generate_safe_filename
        except ImportError:
            from dashboard.bond_pdf_service import generate_appearance_bond, generate_safe_filename

        # Accept both GET query params and POST JSON body
        if request.method == "POST" and request.is_json:
            d = request.get_json(force=True) or {}
            def _p(key, default=""):
                return d.get(key, request.args.get(key, default))
        else:
            def _p(key, default=""):
                return request.args.get(key, default)

        data = {
            "name": _p("name") or _p("defendant_name", ""),
            "first_name": _p("first_name", ""),
            "last_name": _p("last_name", ""),
            "booking_number": _p("booking") or _p("booking_number", ""),
            "county": _p("county", ""),
            "bond_amount": _p("bond") or _p("bond_amount", "0"),
            "charge": _p("charge", ""),
            "surety": _p("surety", "osi"),
            "bond_date": _p("date") or _p("bond_date") or datetime.now().strftime("%m/%d/%Y"),
            "dob": _p("dob") or _p("date_of_birth", ""),
            "address": _p("address", ""),
            "court_date": _p("court_date", ""),
            "court_time": _p("court_time", ""),
            "case_number": _p("case_number", ""),
            "poa_number": _p("poa_number", ""),
            "court_type": _p("court_type", ""),
            "indemnitor_name": _p("indemnitor_name", ""),
        }

        pdf_bytes = generate_appearance_bond(data)
        filename = generate_safe_filename(data)

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except FileNotFoundError as e:
        return jsonify({"error": f"Template not found: {str(e)}. Ensure templates are in templates/ directory."}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500


# ════════════════════════════════════════════════════════════════════════════════
# HEALTH & MAINTENANCE ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """Health check endpoint for Docker HEALTHCHECK and external monitors."""
    try:
        # Quick ping to verify MongoDB is reachable
        db.command("ping")
        mongo_ok = True
    except Exception:
        mongo_ok = False

    total_arrests = 0
    active_counties = 0
    try:
        total_arrests = arrests.estimated_document_count()
        active_counties = len(arrests.distinct("county"))
    except Exception:
        pass

    status = "ok" if mongo_ok else "degraded"
    return jsonify({
        "status": status,
        "mongodb": "connected" if mongo_ok else "disconnected",
        "total_arrests": total_arrests,
        "active_counties": active_counties,
        "uptime_check": datetime.now(timezone.utc).isoformat(),
    }), 200 if mongo_ok else 503

@app.route("/api/cleanup", methods=["POST"])
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Manual Custody Status Override
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/leads/update-custody", methods=["POST"])
def update_custody():
    """Manually override custody status for a defendant.

    Used when a county scraper can't detect release/bond status programmatically.
    Stores audit trail of who changed it and when.
    """
    body = request.get_json(force=True)
    booking_number = body.get("booking_number", "").strip()
    new_status = body.get("custody_status", "").strip()

    if not booking_number:
        return jsonify({"error": "booking_number is required"}), 400

    valid_statuses = ["In Custody", "Not In Custody", "Released", "Bonded Out"]
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400

    try:
        # Get current record for audit trail
        existing = arrests.find_one({"booking_number": booking_number}, {"status": 1, "custody_overrides": 1})
        if not existing:
            return jsonify({"error": f"No record found for booking {booking_number}"}), 404

        old_status = existing.get("status", "Unknown")

        # Build audit entry
        override_entry = {
            "old_status": old_status,
            "new_status": new_status,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "changed_by": body.get("changed_by", "dashboard_user"),
        }

        # Update the record
        result = arrests.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": new_status,
                    "custody_override": True,
                    "custody_override_at": datetime.now(timezone.utc).isoformat(),
                },
                "$push": {
                    "custody_overrides": override_entry,
                },
            },
        )

        if result.modified_count == 0:
            return jsonify({"error": "Record found but not modified"}), 500

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "old_status": old_status,
            "new_status": new_status,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles iMessage Outreach Proxy
# ═══════════════════════════════════════════════════════════════════════════════

def _format_phone(raw):
    """Normalize a US phone number to +1XXXXXXXXXX."""
    digits = re_mod.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None  # invalid


@app.route("/api/imessage/status")
def imessage_status():
    """Check status of all configured BlueBubbles servers."""
    if not BB_SERVERS:
        return jsonify({"connected": False, "servers": [], "reason": "No BlueBubbles servers configured in .env"})

    servers = []
    any_connected = False
    any_private_api = False
    for phone_key, srv in BB_SERVERS.items():
        entry = {"phone": phone_key, "label": srv["label"], "email": srv["email"], "connected": False}
        try:
            r = http_requests.get(
                f"{srv['url']}/api/v1/server/info",
                params={"password": srv["password"]},
                timeout=5,
            )
            data = r.json()
            if r.status_code == 200:
                entry["connected"] = True
                entry["private_api"] = data.get("data", {}).get("private_api", False)
                entry["os_version"] = data.get("data", {}).get("os_version", "")
                any_connected = True
                if entry.get("private_api"):
                    any_private_api = True
        except Exception:
            entry["error"] = "unreachable"
        servers.append(entry)

    return jsonify({
        "connected": any_connected,
        "private_api": any_private_api,
        "server_count": len(BB_SERVERS),
        "servers": servers,
    })


@app.route("/api/imessage/send", methods=["POST"])
def imessage_send():
    """Send an iMessage via BlueBubbles server. Routes to correct server based on from_number."""
    if not BB_SERVERS:
        return jsonify({"error": "No BlueBubbles servers configured. Set BLUEBUBBLES_URL_0178 and BLUEBUBBLES_PASSWORD_0178 in .env"}), 503

    body = request.get_json(force=True)
    phone_raw = body.get("phone", "")
    message = body.get("message", "").strip()
    booking_number = body.get("booking_number", "")
    defendant_name = body.get("defendant_name", "")
    county = body.get("county", "")
    recipient_label = body.get("recipient_label", "Unknown")
    agent_name = body.get("agent_name", "Brendan")
    from_number = body.get("from_number", "2399550178")

    if not phone_raw or not message:
        return jsonify({"error": "phone and message are required"}), 400

    phone = _format_phone(phone_raw)
    if not phone:
        return jsonify({"error": f"Invalid phone number: {phone_raw}"}), 400

    # Route to the correct BlueBubbles server based on from_number
    srv = _get_bb_server(from_number)
    if not srv:
        return jsonify({"error": f"No BlueBubbles server configured for {from_number}"}), 503

    chat_guid = f"any;-;{phone}"
    temp_guid = f"shamrock-{uuid.uuid4().hex[:16]}"

    try:
        r = http_requests.post(
            f"{srv['url']}/api/v1/message/text",
            params={"password": srv["password"]},
            json={
                "chatGuid": chat_guid,
                "tempGuid": temp_guid,
                "message": message,
            },
            timeout=15,
        )
        bb_resp = r.json()
        success = r.status_code in (200, 201)

        # Log to MongoDB
        doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "recipient_phone": phone,
            "recipient_label": recipient_label,
            "message": message,
            "chat_guid": chat_guid,
            "temp_guid": temp_guid,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent" if success else "failed",
            "bb_status_code": r.status_code,
            "bb_error": bb_resp.get("message", "") if not success else "",
            "sent_by": "dashboard",
            "agent_name": agent_name,
            "from_number": from_number,
            "from_email": srv.get("email", ""),
        }
        imessage_outreach.insert_one(doc)
        doc.pop("_id", None)

        if success:
            return jsonify({"success": True, "record": doc})
        else:
            return jsonify({"success": False, "error": bb_resp.get("message", "BlueBubbles error"), "record": doc}), 502

    except http_requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot reach BlueBubbles server. Is it running?"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/imessage/history/<booking_number>")
def imessage_history(booking_number):
    """Get outreach message history for a defendant."""
    docs = list(
        imessage_outreach.find(
            {"booking_number": booking_number},
            {"_id": 0},
        ).sort("sent_at", -1).limit(50)
    )
    return jsonify({"messages": docs, "count": len(docs)})


@app.route("/api/imessage/templates")
def imessage_templates():
    """Return available outreach message templates."""
    templates = [
        {
            "id": "standard",
            "name": "Standard Outreach",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. I see that {name} is currently in custody in the {county} County Jail. We were wondering if you'd like some help bonding them out of jail.",
        },
        {
            "id": "urgent",
            "name": "Urgent / High Bond",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. I see that {name} is currently being held in {county} County on a significant bond. We specialize in getting people home fast with flexible payment plans. Would you like some help?",
        },
        {
            "id": "followup",
            "name": "Follow-Up",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds, just following up about {name} in {county} County. We're still available to help if you'd like to get them out. No obligation to chat.",
        },
    ]
    return jsonify({"templates": templates})


if __name__ == "__main__":
    print("\n🍀 ShamrockLeads Intelligence Dashboard")
    print("   http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)
