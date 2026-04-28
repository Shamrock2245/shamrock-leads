"""
ShamrockLeads — Stats API Blueprint
Endpoints: /api/status, /api/mongo-stats, /api/command, /api/stats,
           /api/bond-distribution, /api/top-charges, /api/bounty-board,
           /api/timeline, /api/scraper-health, /api/leads/<booking_number>
"""

import csv
import io
import re as re_mod
from datetime import datetime, timezone, timedelta

from quart import Blueprint, jsonify, request, Response
from dashboard.extensions import get_collection, REGISTERED_COUNTIES

stats_bp = Blueprint("stats", __name__)


def _build_leads_query():
    """Shared query builder for /api/leads and /api/leads/export."""
    arrests = get_collection("arrests")
    query = {}

    status_filter = request.args.get("status", "").strip()
    if status_filter:
        query["lead_status"] = status_filter

    county_filter = request.args.get("county", "").strip()
    if county_filter:
        counties = [c.strip() for c in county_filter.split(",") if c.strip()]
        if len(counties) == 1:
            query["county"] = counties[0]
        elif len(counties) > 1:
            query["county"] = {"$in": counties}

    custody_param = request.args.get("custody", "").strip().lower()
    if custody_param == "true":
        query["status"] = {"$regex": "custody|confined|held", "$options": "i"}
    elif custody_param == "released":
        query["status"] = {"$regex": "released|bonded|rts", "$options": "i"}

    days_param = request.args.get("days", "").strip()
    if days_param:
        try:
            days_int = int(days_param)
            if 1 <= days_int <= 30:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days_int)
                query["scraped_at"] = {"$gte": cutoff.strftime("%Y-%m-%dT%H:%M:%S")}
        except (ValueError, TypeError):
            pass

    min_bond = request.args.get("min_bond", "").strip()
    if min_bond:
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass

    search = request.args.get("search", "").strip()
    if search:
        pattern = re_mod.compile(re_mod.escape(search), re_mod.IGNORECASE)
        search_or = [
            {"full_name": {"$regex": pattern}},
            {"charges": {"$regex": pattern}},
            {"booking_number": {"$regex": pattern}},
            {"case_number": {"$regex": pattern}},
        ]
        if "$or" in query and query["$or"]:
            existing_or = query.pop("$or")
            query["$and"] = [{"$or": existing_or}, {"$or": search_or}]
        else:
            query.pop("$or", None)
            query["$or"] = search_or

    return query, status_filter, county_filter, search


@stats_bp.route("/status")
async def api_status():
    """
    Returns scraper fleet status for the Scraper Health tab.

    Priority order:
    1. scraper_status collection (written by base_scraper after every run)
    2. arrests collection aggregate (fallback for counties with records but no status doc)
    3. REGISTERED_COUNTIES stub (never_run for counties with no data at all)

    This ensures all 49 registered counties always appear in the dashboard.
    """
    scraper_status_col = get_collection("scraper_status")
    arrests = get_collection("arrests")
    try:
        # ── Layer 1: Read from scraper_status collection ──
        scrapers = {}
        async for doc in scraper_status_col.find({}, {"_id": 0}):
            county = doc.get("county")
            if not county:
                continue
            last_run = doc.get("last_run")
            scrapers[county] = {
                "last_run": last_run.isoformat() if isinstance(last_run, datetime) else str(last_run or ""),
                "records": doc.get("records", 0),
                "hot_leads": doc.get("hot_leads", 0),
                "warm_leads": doc.get("warm_leads", 0),
                "cold_leads": doc.get("cold_leads", 0),
                "disqualified": doc.get("disqualified", 0),
                "duration_seconds": doc.get("duration_seconds", 0),
                "status": doc.get("status", "ok"),
                "error": doc.get("error"),
                "run_count": doc.get("run_count", 1),
                "total_records": doc.get("records", 0),
                "source": "live",
            }

        # ── Layer 2: Fill in counties with arrest records but no status doc ──
        pipeline = [
            {"$group": {
                "_id": "$county",
                "records": {"$sum": 1},
                "latest": {"$max": {"$ifNull": ["$updated_at", "$created_at"]}},
                "hot": {"$sum": {"$cond": [{"$gte": ["$lead_score", 70]}, 1, 0]}},
                "warm": {"$sum": {"$cond": [{"$and": [{"$gte": ["$lead_score", 40]}, {"$lt": ["$lead_score", 70]}]}, 1, 0]}},
            }},
        ]
        async for r in arrests.aggregate(pipeline):
            county = r["_id"]
            if not county or county in scrapers:
                continue  # Already have live status
            latest = r.get("latest")
            scrapers[county] = {
                "last_run": latest.isoformat() if isinstance(latest, datetime) else str(latest or ""),
                "records": r["records"],
                "hot_leads": r["hot"],
                "warm_leads": r["warm"],
                "cold_leads": 0,
                "disqualified": 0,
                "duration_seconds": 0,
                "status": "ok",
                "error": None,
                "run_count": 1,
                "total_records": r["records"],
                "source": "arrests_aggregate",
            }

        # ── Layer 3: Fill in all 49 registered counties with never_run stub ──
        for county in REGISTERED_COUNTIES:
            if county not in scrapers:
                scrapers[county] = {
                    "last_run": None,
                    "records": 0,
                    "hot_leads": 0,
                    "warm_leads": 0,
                    "cold_leads": 0,
                    "disqualified": 0,
                    "duration_seconds": 0,
                    "status": "never_run",
                    "error": None,
                    "run_count": 0,
                    "total_records": 0,
                    "source": "registered",
                }

        total_scraped = sum(s["total_records"] for s in scrapers.values())
        active_count = sum(1 for s in scrapers.values() if s["status"] == "ok")
        error_count = sum(1 for s in scrapers.values() if s["status"] == "error")
        never_run_count = sum(1 for s in scrapers.values() if s["status"] == "never_run")

        return jsonify({
            "started_at": datetime.now(timezone.utc).isoformat(),
            "scrapers": scrapers,
            "total_scraped": total_scraped,
            "total_registered": len(REGISTERED_COUNTIES),
            "active_count": active_count,
            "error_count": error_count,
            "never_run_count": never_run_count,
            "total_hot_leads": sum(s["hot_leads"] for s in scrapers.values()),
            "total_warm_leads": sum(s["warm_leads"] for s in scrapers.values()),
            "cycle_count": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stats_bp.route("/mongo-stats")
async def api_mongo_stats():
    """MongoDB record stats for the dashboard."""
    arrests = get_collection("arrests")
    try:
        total = await arrests.count_documents({})
        by_county = {c: 0 for c in REGISTERED_COUNTIES}
        async for doc in arrests.aggregate([
            {"$group": {"_id": "$county", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]):
            by_county[doc["_id"]] = doc["count"]

        hot = await arrests.count_documents({"lead_score": {"$gte": 70}})
        warm = await arrests.count_documents({"lead_score": {"$gte": 40, "$lt": 70}})
        cold = await arrests.count_documents({"lead_score": {"$gte": 20, "$lt": 40}})
        disq = await arrests.count_documents({"lead_score": {"$lt": 20}})

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_hot = []
        async for doc in arrests.find(
            {"lead_score": {"$gte": 70}, "scraped_at": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "bond_type": 1},
        ).sort("lead_score", -1).limit(20):
            recent_hot.append(doc)

        return jsonify({
            "total_records": total, "by_county": by_county,
            "scores": {"hot": hot, "warm": warm, "cold": cold, "disqualified": disq},
            "recent_hot_leads": recent_hot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stats_bp.route("/command")
async def api_command_center():
    """Rich command center — actionable leads, bond-ready queue, revenue pipeline."""
    arrests = get_collection("arrests")
    try:
        bond_ready = []
        async for doc in arrests.find(
            {"status": {"$regex": "custody|confined|held", "$options": "i"},
             "bond_amount": {"$gte": 1000}, "lead_score": {"$gte": 40}},
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "lead_status": 1,
             "status": 1, "booking_number": 1, "dob": 1, "arrest_date": 1,
             "booking_date": 1, "bond_type": 1, "detail_url": 1},
        ).sort("bond_amount", -1).limit(25):
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            bond_ready.append(doc)

        pipeline_total = sum(d.get("bond_amount", 0) for d in bond_ready)
        premium_est = sum(max(100, d.get("bond_amount", 0) * 0.1) for d in bond_ready)

        recent = []
        async for doc in arrests.find(
            {}, {"_id": 0, "full_name": 1, "county": 1, "bond_amount": 1,
                 "lead_score": 1, "lead_status": 1, "scraped_at": 1,
                 "status": 1, "charges": 1},
        ).sort("scraped_at", -1).limit(10):
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            recent.append(doc)

        custody_by_county = []
        async for d in arrests.aggregate([
            {"$match": {"status": {"$regex": "custody|confined|held", "$options": "i"}}},
            {"$group": {"_id": "$county", "count": {"$sum": 1},
                        "total_bond": {"$sum": "$bond_amount"}}},
            {"$sort": {"total_bond": -1}},
        ]):
            if d["_id"]:
                custody_by_county.append({"county": d["_id"], "count": d["count"],
                                          "total_bond": d.get("total_bond", 0)})

        return jsonify({
            "bond_ready": bond_ready, "pipeline_total": pipeline_total,
            "premium_estimate": premium_est, "bond_ready_count": len(bond_ready),
            "recent_activity": recent, "custody_by_county": custody_by_county,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stats_bp.route("/leads")
async def api_leads():
    """Filterable, sortable leads list with multi-county and date range support."""
    arrests = get_collection("arrests")
    try:
        query, status_filter, county_filter, search = _build_leads_query()
        sort_field = request.args.get("sort", "lead_score").strip()
        sort_order = -1 if request.args.get("order", "desc").strip() == "desc" else 1
        sort_map = {
            "lead_score": "lead_score", "bond_amount": "bond_amount",
            "booking_date": "booking_date", "full_name": "full_name",
            "county": "county", "arrest_date": "arrest_date", "created_at": "created_at",
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
        total_matching = await arrests.count_documents(query)
        leads_list = []
        async for doc in arrests.find(query, projection).sort(mongo_sort, sort_order).skip(skip).limit(limit):
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            leads_list.append(doc)

        db_counties = await arrests.distinct("county")
        counties_list = sorted(set(REGISTERED_COUNTIES + [c for c in db_counties if c]))

        return jsonify({
            "leads": leads_list, "total": total_matching,
            "page": page, "limit": limit,
            "pages": max(1, (total_matching + limit - 1) // limit),
            "counties": counties_list,
            "query": {"status": status_filter, "county": county_filter,
                      "search": search, "sort": sort_field,
                      "order": "desc" if sort_order == -1 else "asc"},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stats_bp.route("/leads/export")
async def api_leads_export():
    """CSV export of current filtered leads."""
    arrests = get_collection("arrests")
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

        columns = [
            "full_name", "county", "charges", "bond_amount", "bond_type",
            "lead_score", "lead_status", "status", "booking_number",
            "arrest_date", "booking_date", "court_date", "court_location",
            "case_number", "dob", "sex", "race", "address", "facility", "detail_url",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        async for doc in arrests.find(query, {"_id": 0}).sort(mongo_sort, sort_order).limit(5000):
            for k, v in doc.items():
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            writer.writerow(doc)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return Response(
            output.getvalue(), content_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=shamrock_leads_{timestamp}.csv"},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stats_bp.route("/leads/<booking_number>")
async def api_lead_detail(booking_number):
    """Get full detail for a single lead by booking number."""
    arrests = get_collection("arrests")
    try:
        doc = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not doc:
            try:
                doc = await arrests.find_one({"booking_number": int(booking_number)}, {"_id": 0})
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


@stats_bp.route("/stats")
async def api_overview_stats():
    """High-level dashboard stats."""
    arrests = get_collection("arrests")
    total = await arrests.count_documents({})
    counties = await arrests.distinct("county")
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = await arrests.count_documents({"created_at": {"$gte": today_start}})

    bond_stats = []
    async for r in arrests.aggregate([
        {"$match": {"bond_amount": {"$gt": 0}}},
        {"$group": {"_id": None, "avg_bond": {"$avg": "$bond_amount"},
                    "max_bond": {"$max": "$bond_amount"}, "total_bond": {"$sum": "$bond_amount"}}},
    ]):
        bond_stats.append(r)
    bond = bond_stats[0] if bond_stats else {"avg_bond": 0, "max_bond": 0, "total_bond": 0}
    high_value = await arrests.count_documents({"bond_amount": {"$gte": 2500}})

    return jsonify({
        "total_arrests": total, "counties_active": len(counties),
        "today_new": today_count,
        "avg_bond": round(bond.get("avg_bond", 0), 2),
        "max_bond": round(bond.get("max_bond", 0), 2),
        "total_bond_value": round(bond.get("total_bond", 0), 2),
        "high_value_leads": high_value,
    })


@stats_bp.route("/arrests")
async def api_arrests_list():
    """Paginated, filterable arrest list."""
    arrests = get_collection("arrests")
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

    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0, "extra": 0}).sort(sort_by, sort_dir).skip((page - 1) * limit).limit(limit):
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({"arrests": results, "total": total, "page": page,
                    "pages": max(1, (total + limit - 1) // limit)})


@stats_bp.route("/bond-distribution")
async def api_bond_distribution():
    """Bond amount distribution for charts."""
    arrests = get_collection("arrests")
    results = []
    async for r in arrests.aggregate([
        {"$match": {"bond_amount": {"$gt": 0}}},
        {"$bucket": {
            "groupBy": "$bond_amount",
            "boundaries": [0, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 500000, 1000001],
            "default": "1000001+",
            "output": {"count": {"$sum": 1}},
        }},
    ]):
        results.append(r)

    labels = ["$0-500", "$500-1K", "$1K-2.5K", "$2.5K-5K", "$5K-10K",
              "$10K-25K", "$25K-50K", "$50K-100K", "$100K-500K", "$500K+"]
    counts = [0] * len(labels)
    idx_map = {0: 0, 500: 1, 1000: 2, 2500: 3, 5000: 4, 10000: 5, 25000: 6, 50000: 7, 100000: 8, 500000: 9}
    for r in results:
        idx = idx_map.get(r["_id"], 9)
        counts[idx] = r["count"]
    return jsonify({"labels": labels, "counts": counts})


@stats_bp.route("/top-charges")
async def api_top_charges():
    """Most common charge keywords."""
    arrests = get_collection("arrests")
    results = []
    async for r in arrests.aggregate([
        {"$match": {"charges": {"$exists": True, "$ne": ""}}},
        {"$project": {"words": {"$split": [{"$toUpper": "$charges"}, " | "]}}},
        {"$unwind": "$words"},
        {"$group": {"_id": "$words", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]):
        results.append({"charge": r["_id"], "count": r["count"]})
    return jsonify(results)


@stats_bp.route("/bounty-board")
async def api_bounty_board():
    """High-value unposted bonds (>$2,500)."""
    arrests = get_collection("arrests")
    sort_by = request.args.get("sort", "bond_amount")
    sort_dir = int(request.args.get("dir", -1))
    county = request.args.get("county", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))

    query = {"bond_amount": {"$gte": 2500}, "bond_paid": {"$nin": ["YES", "POSTED", "BONDED"]}}
    if county:
        query["county"] = county

    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort(sort_by, sort_dir).skip((page - 1) * limit).limit(limit):
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({"targets": results, "total": total, "page": page,
                    "pages": max(1, (total + limit - 1) // limit)})


@stats_bp.route("/timeline")
async def api_timeline():
    """Scraping activity over time (by date)."""
    arrests = get_collection("arrests")
    results = []
    async for r in arrests.aggregate([
        {"$addFields": {"date_str": {
            "$cond": {
                "if": {"$ne": [{"$type": "$created_at"}, "missing"]},
                "then": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "else": "$booking_date",
            }
        }}},
        {"$group": {"_id": {"date": "$date_str", "county": "$county"}, "count": {"$sum": 1}}},
        {"$sort": {"_id.date": 1}},
    ]):
        results.append(r)

    dates = sorted(set(r["_id"]["date"] for r in results if r["_id"]["date"]))
    counties = sorted(set(r["_id"]["county"] for r in results if r["_id"]["county"]))
    series = {c: {d: 0 for d in dates} for c in counties}
    for r in results:
        d, c = r["_id"].get("date"), r["_id"].get("county")
        if d and c and c in series:
            series[c][d] = r["count"]

    return jsonify({"dates": dates, "series": {c: list(series[c].values()) for c in counties}})


@stats_bp.route("/scraper-health")
async def api_scraper_health():
    """Per-county scraper health metrics — always returns all registered counties."""
    try:
        scraper_status_col = get_collection("scraper_status")
        scraper_config_col = get_collection("scraper_config")
        arrests = get_collection("arrests")
        now = datetime.now(timezone.utc)
        h24_ago = now - timedelta(hours=24)

        # Build base from arrests aggregate
        results_map = {}
        async for r in arrests.aggregate([
            {"$group": {
                "_id": "$county",
                "total_records": {"$sum": 1},
                "latest_record": {"$max": "$created_at"},
                "latest_scrape": {"$max": "$scrape_timestamp"},
                "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
                "max_bond": {"$max": "$bond_amount"},
                "total_bond": {"$sum": "$bond_amount"},
                "in_custody": {"$sum": {"$cond": [
                    {"$in": [{"$toLower": {"$ifNull": ["$custody_status", ""]}}, ["in custody", "in-custody", "incustody", "confined", "held", "booked"]]},
                    1, 0,
                ]}},
                "hot_leads": {"$sum": {"$cond": [{"$gte": ["$lead_score", 70]}, 1, 0]}},
                "warm_leads": {"$sum": {"$cond": [{"$and": [{"$gte": ["$lead_score", 40]}, {"$lt": ["$lead_score", 70]}]}, 1, 0]}},
            }},
        ]):
            results_map[r["_id"]] = r

        counts_24h = {}
        async for r in arrests.aggregate([
            {"$match": {"created_at": {"$gte": h24_ago}}},
            {"$group": {"_id": "$county", "count_24h": {"$sum": 1}}},
        ]):
            counts_24h[r["_id"]] = r["count_24h"]

        # Overlay live run data from scraper_status collection
        live_status = {}
        async for doc in scraper_status_col.find({}, {"_id": 0}):
            county = doc.get("county")
            if county:
                live_status[county] = doc

        # Load enabled/disabled config
        config_map = {}
        async for doc in scraper_config_col.find({}, {"_id": 0}):
            county = doc.get("county")
            if county:
                config_map[county] = doc

        out = []
        seen = set()

        # Process counties that have arrest records
        for county, r in sorted(results_map.items(), key=lambda x: -x[1]["total_records"]):
            if not county:
                continue
            seen.add(county)
            latest = r.get("latest_record") or r.get("latest_scrape")
            hours_since = (now - latest).total_seconds() / 3600 if isinstance(latest, datetime) else 999
            base_status = "healthy" if hours_since < 2 else "stale" if hours_since < 6 else "warning" if hours_since < 24 else "offline"
            live = live_status.get(county, {})
            cfg = config_map.get(county, {})
            if cfg.get("enabled") is False:
                base_status = "disabled"
            elif live.get("status") == "error":
                base_status = "error"
            elif live.get("status") == "ok" and hours_since < 2:
                base_status = "healthy"
            last_run = live.get("last_run")
            out.append({
                "county": county,
                "total_records": r["total_records"],
                "in_custody": r["in_custody"],
                "records_24h": counts_24h.get(county, 0),
                "latest_record": latest.isoformat() if isinstance(latest, datetime) else str(latest or ""),
                "last_run": last_run.isoformat() if isinstance(last_run, datetime) else str(last_run or ""),
                "hours_since_update": round(hours_since, 1),
                "status": base_status,
                "avg_bond": round(r.get("avg_bond") or 0, 2),
                "max_bond": round(r.get("max_bond") or 0, 2),
                "total_bond": round(r.get("total_bond") or 0, 2),
                "hot_leads": r.get("hot_leads", 0),
                "warm_leads": r.get("warm_leads", 0),
                "duration_seconds": live.get("duration_seconds", 0),
                "run_count": live.get("run_count", 0),
                "error": live.get("error"),
                "enabled": cfg.get("enabled", True),
            })

        # Add registered counties with no arrest records yet
        for county in sorted(REGISTERED_COUNTIES):
            if county in seen:
                continue
            live = live_status.get(county, {})
            cfg = config_map.get(county, {})
            run_status = "disabled" if cfg.get("enabled") is False else live.get("status", "never_run")
            last_run = live.get("last_run")
            out.append({
                "county": county,
                "total_records": 0,
                "in_custody": 0,
                "records_24h": 0,
                "latest_record": "",
                "last_run": last_run.isoformat() if isinstance(last_run, datetime) else str(last_run or ""),
                "hours_since_update": 999,
                "status": run_status,
                "avg_bond": 0,
                "max_bond": 0,
                "total_bond": 0,
                "hot_leads": 0,
                "warm_leads": 0,
                "duration_seconds": live.get("duration_seconds", 0),
                "run_count": live.get("run_count", 0),
                "error": live.get("error"),
                "enabled": cfg.get("enabled", True),
            })

        return jsonify(out)
    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "trace": traceback.format_exc()}), 500


@stats_bp.route("/counties")
async def api_counties_stats():
    """Per-county stats with in-custody counts."""
    arrests = get_collection("arrests")
    results = []
    async for r in arrests.aggregate([
        {"$group": {
            "_id": "$county", "total": {"$sum": 1},
            "in_custody": {"$sum": {"$cond": [
                {"$regexMatch": {"input": {"$ifNull": ["$status", ""]}, "regex": "custody|confined|held", "options": "i"}},
                1, 0,
            ]}},
            "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
            "max_bond": {"$max": "$bond_amount"}, "total_bond": {"$sum": "$bond_amount"},
            "latest_scrape": {"$max": "$scrape_timestamp"},
        }},
        {"$sort": {"total": -1}},
    ]):
        results.append({
            "county": r["_id"], "total": r["total"], "in_custody": r["in_custody"],
            "avg_bond": round(r["avg_bond"] or 0, 2), "max_bond": round(r["max_bond"] or 0, 2),
            "total_bond": round(r["total_bond"] or 0, 2), "latest_scrape": r["latest_scrape"],
        })
    return jsonify(results)
