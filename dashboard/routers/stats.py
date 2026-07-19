from __future__ import annotations
"""Stats Router — FastAPI port of api/stats.py (13 endpoints)"""
import logging
import re as re_mod
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse

from dashboard.deps import get_collection
from dashboard.extensions import REGISTERED_COUNTIES
from dashboard.routers.helpers import serialize_doc, async_csv_streamer
from dashboard.models.leads import LeadsQueryModel

logger = logging.getLogger("shamrock.stats")

router = APIRouter(prefix="/api", tags=["stats"])


def _build_leads_query(query: LeadsQueryModel):
    from dashboard.extensions import parse_registered_county

    q: dict = {}
    if query.status:
        q["lead_status"] = query.status
    if getattr(query, "state", None):
        states = [s.strip().upper() for s in query.state.split(",") if s.strip()]
        if len(states) == 1:
            q["state"] = states[0]
        elif states:
            q["state"] = {"$in": states}
    if query.county:
        cs = [c.strip() for c in query.county.split(",") if c.strip()]
        clauses = []
        bare_only = []
        for c in cs:
            name, st = parse_registered_county(c)
            if st:
                clauses.append({"county": name, "state": st})
            else:
                bare_only.append(name)
        if bare_only:
            if len(bare_only) == 1:
                clauses.append({"county": bare_only[0]})
            else:
                clauses.append({"county": {"$in": bare_only}})
        if len(clauses) == 1:
            # Merge single clause into top-level query
            for k, v in clauses[0].items():
                # If state already set and clause also has state, prefer clause
                q[k] = v
        elif clauses:
            q["$or"] = clauses
    if query.custody == "true":
        q["status"] = {"$regex": "custody|confined|held", "$options": "i"}
    elif query.custody == "released":
        q["status"] = {"$regex": "released|bonded|rts", "$options": "i"}
    if query.days:
        cut = datetime.now(timezone.utc) - timedelta(days=query.days)
        q["scraped_at"] = {"$gte": cut.strftime("%Y-%m-%dT%H:%M:%S")}
    if query.min_bond is not None:
        q["bond_amount"] = {"$gte": query.min_bond}
    if query.search:
        escaped = re_mod.escape(query.search)
        sor = [
            {"full_name": {"$regex": escaped, "$options": "i"}},
            {"first_name": {"$regex": escaped, "$options": "i"}},
            {"last_name": {"$regex": escaped, "$options": "i"}},
            {"charges": {"$regex": escaped, "$options": "i"}},
            {"booking_number": {"$regex": escaped, "$options": "i"}},
            {"case_number": {"$regex": escaped, "$options": "i"}},
            {"address": {"$regex": escaped, "$options": "i"}},
            {"county": {"$regex": escaped, "$options": "i"}},
            {"state": {"$regex": escaped, "$options": "i"}},
        ]
        if "$or" in q:
            existing = q.pop("$or")
            q["$and"] = [{"$or": existing}, {"$or": sor}]
        else:
            q["$or"] = sor
    return q


@router.get("/status")
async def api_status():
    scraper_status_col = get_collection("scraper_status")
    arrests = get_collection("arrests")
    try:
        scrapers = {}
        async for doc in scraper_status_col.find({}, {"_id": 0}):
            county = doc.get("county")
            if not county:
                continue
            lr = doc.get("last_run")
            scrapers[county] = {
                "last_run": lr.isoformat() if isinstance(lr, datetime) else str(lr or ""),
                "records": doc.get("records", 0), "hot_leads": doc.get("hot_leads", 0),
                "warm_leads": doc.get("warm_leads", 0), "cold_leads": doc.get("cold_leads", 0),
                "disqualified": doc.get("disqualified", 0),
                "duration_seconds": doc.get("duration_seconds", 0),
                "status": doc.get("status", "ok"), "error": doc.get("error"),
                "run_count": doc.get("run_count", 1),
                "total_records": doc.get("records", 0), "source": "live",
            }
        pipeline = [
            {"$group": {"_id": "$county", "records": {"$sum": 1},
                        "latest": {"$max": {"$ifNull": ["$updated_at", "$created_at"]}},
                        "hot": {"$sum": {"$cond": [{"$gte": ["$lead_score", 70]}, 1, 0]}},
                        "warm": {"$sum": {"$cond": [{"$and": [{"$gte": ["$lead_score", 40]}, {"$lt": ["$lead_score", 70]}]}, 1, 0]}}}},
        ]
        async for r in arrests.aggregate(pipeline):
            county = r["_id"]
            if not county or county in scrapers:
                continue
            lt = r.get("latest")
            scrapers[county] = {
                "last_run": lt.isoformat() if isinstance(lt, datetime) else str(lt or ""),
                "records": r["records"], "hot_leads": r["hot"], "warm_leads": r["warm"],
                "cold_leads": 0, "disqualified": 0, "duration_seconds": 0,
                "status": "ok", "error": None, "run_count": 1,
                "total_records": r["records"], "source": "arrests_aggregate",
            }
        for county in REGISTERED_COUNTIES:
            if county not in scrapers:
                scrapers[county] = {
                    "last_run": None, "records": 0, "hot_leads": 0, "warm_leads": 0,
                    "cold_leads": 0, "disqualified": 0, "duration_seconds": 0,
                    "status": "never_run", "error": None, "run_count": 0,
                    "total_records": 0, "source": "registered",
                }
        return {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "scrapers": scrapers,
            "total_scraped": sum(s["total_records"] for s in scrapers.values()),
            "total_registered": len(REGISTERED_COUNTIES),
            "active_count": sum(1 for s in scrapers.values() if s["status"] == "ok"),
            "error_count": sum(1 for s in scrapers.values() if s["status"] == "error"),
            "never_run_count": sum(1 for s in scrapers.values() if s["status"] == "never_run"),
            "total_hot_leads": sum(s["hot_leads"] for s in scrapers.values()),
            "total_warm_leads": sum(s["warm_leads"] for s in scrapers.values()),
            "cycle_count": 0,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/mongo-stats")
async def api_mongo_stats():
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
        return {
            "total_records": total, "by_county": by_county,
            "scores": {"hot": hot, "warm": warm, "cold": cold, "disqualified": disq},
            "recent_hot_leads": recent_hot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/command")
async def api_command_center():
    arrests = get_collection("arrests")
    try:
        bond_ready = []
        async for doc in arrests.find(
            {"status": {"$regex": "custody|confined|held", "$options": "i"},
             "bond_amount": {"$gte": 1000}, "lead_score": {"$gte": 40}},
            {"_id": 0, "full_name": 1, "county": 1, "state": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "lead_status": 1,
             "status": 1, "booking_number": 1, "dob": 1, "arrest_date": 1,
             "booking_date": 1, "bond_type": 1, "detail_url": 1},
        ).sort("bond_amount", -1).limit(25):
            bond_ready.append(serialize_doc(doc))
        pipeline_total = sum(d.get("bond_amount", 0) for d in bond_ready)
        premium_est = sum(max(100, d.get("bond_amount", 0) * 0.1) for d in bond_ready)
        recent = []
        async for doc in arrests.find(
            {}, {"_id": 0, "full_name": 1, "county": 1, "state": 1, "bond_amount": 1,
                 "lead_score": 1, "lead_status": 1, "scraped_at": 1,
                 "status": 1, "charges": 1},
        ).sort("scraped_at", -1).limit(10):
            recent.append(serialize_doc(doc))
        custody_by_county = []
        async for d in arrests.aggregate([
            {"$match": {"status": {"$regex": "custody|confined|held", "$options": "i"}}},
            {"$group": {"_id": {"county": "$county", "state": "$state"},
                        "count": {"$sum": 1},
                        "total_bond": {"$sum": "$bond_amount"}}},
            {"$sort": {"total_bond": -1}},
        ]):
            grp = d.get("_id") or {}
            if grp.get("county"):
                custody_by_county.append({
                    "county": grp["county"],
                    "state": (grp.get("state") or "FL").upper(),
                    "count": d["count"],
                    "total_bond": d.get("total_bond", 0),
                })
        # State-level breakdown for Command Center KPI row
        now = datetime.now(timezone.utc)
        h24 = now - timedelta(hours=24)
        state_breakdown: dict = {}
        for st in ("FL", "GA", "SC", "NC"):
            state_breakdown[st] = {"total": 0, "last_24h": 0, "hot_leads": 0, "pipeline": 0}
        async for d in arrests.aggregate([
            {"$group": {"_id": {"$toUpper": {"$ifNull": ["$state", "FL"]}},
                        "total": {"$sum": 1},
                        "hot": {"$sum": {"$cond": [{"$gte": ["$lead_score", 70]}, 1, 0]}},
                        "pipeline": {"$sum": {"$cond": [
                            {"$and": [
                                {"$gte": ["$bond_amount", 1000]},
                                {"$gte": ["$lead_score", 40]},
                            ]}, "$bond_amount", 0]}}}},
        ]):
            st = d["_id"] or "FL"
            if st in state_breakdown:
                state_breakdown[st].update({"total": d["total"], "hot_leads": d["hot"],
                                            "pipeline": round(d["pipeline"], 2)})
        async for d in arrests.aggregate([
            {"$match": {"$or": [
                {"scraped_at": {"$gte": h24}},
                {"scraped_at": {"$gte": h24.isoformat()}},
            ]}},
            {"$group": {"_id": {"$toUpper": {"$ifNull": ["$state", "FL"]}},
                        "count": {"$sum": 1}}},
        ]):
            st = d["_id"] or "FL"
            if st in state_breakdown:
                state_breakdown[st]["last_24h"] = d["count"]
        return {
            "bond_ready": bond_ready, "pipeline_total": pipeline_total,
            "premium_estimate": premium_est, "bond_ready_count": len(bond_ready),
            "recent_activity": recent, "custody_by_county": custody_by_county,
            "state_breakdown": state_breakdown,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("[command] error: %s", type(exc).__name__, exc_info=True)
        return {"error": "Internal server error"}


@router.get("/leads")
async def api_leads(
    query: LeadsQueryModel = Depends(),
):
    """Filterable, sortable leads list.

    Frontend (sl-data.js) defaults sort to ``scraped_at`` and expects an
    ``activity.scraped_last_hour`` field for the results meta line.
    """
    arrests = get_collection("arrests")
    try:
        mongo_query = _build_leads_query(query)
        # Align with frontend default (scraped_at) and LeadsQueryModel default.
        sort_map = {
            "scraped_at": "scraped_at",
            "lead_score": "lead_score",
            "bond_amount": "bond_amount",
            "booking_date": "booking_date",
            "full_name": "full_name",
            "county": "county",
            "arrest_date": "arrest_date",
            "created_at": "created_at",
            "state": "state",
            "lead_status": "lead_status",
        }
        mongo_sort = sort_map.get(query.sort or "scraped_at", "scraped_at")
        sort_order = -1 if query.order == "desc" else 1
        skip = (query.page - 1) * query.limit
        projection = {
            "_id": 0, "full_name": 1, "first_name": 1, "last_name": 1,
            "booking_number": 1, "county": 1, "state": 1, "charges": 1, "bond_amount": 1,
            "bond_type": 1, "lead_score": 1, "lead_status": 1, "status": 1,
            "arrest_date": 1, "booking_date": 1, "court_date": 1,
            "court_location": 1, "case_number": 1, "dob": 1, "sex": 1,
            "race": 1, "address": 1, "detail_url": 1, "facility": 1,
            "mugshot_url": 1, "scraped_at": 1, "created_at": 1,
        }
        total = await arrests.count_documents(mongo_query)
        leads_list = []
        async for doc in arrests.find(mongo_query, projection).sort(mongo_sort, sort_order).skip(skip).limit(query.limit):
            leads_list.append(serialize_doc(doc))
        db_counties = await arrests.distinct("county")
        counties_list = sorted(set(REGISTERED_COUNTIES + [c for c in db_counties if c]))

        # Real-time activity for frontend meta (sl-data.js resultsMeta line).
        # scraped_at may be stored as datetime or ISO string — match both.
        hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        scraped_last_hour = await arrests.count_documents({
            "$or": [
                {"scraped_at": {"$gte": hour_ago}},
                {"scraped_at": {"$gte": hour_ago.isoformat()}},
            ]
        })

        return {
            "leads": leads_list, "total": total, "page": query.page, "limit": query.limit,
            "pages": max(1, (total + query.limit - 1) // query.limit),
            "counties": counties_list,
            "activity": {
                "scraped_last_hour": scraped_last_hour,
            },
            "query": {
                "status": query.status,
                "county": query.county,
                "state": getattr(query, "state", "") or "",
                "search": query.search,
                "sort": query.sort or "scraped_at",
                "order": query.order,
            },
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/leads/export")
async def api_leads_export(
    query: LeadsQueryModel = Depends(),
):
    arrests = get_collection("arrests")
    try:
        mongo_query = _build_leads_query(query)
        sort_map = {
            "scraped_at": "scraped_at",
            "lead_score": "lead_score",
            "bond_amount": "bond_amount",
            "booking_date": "booking_date",
            "full_name": "full_name",
            "county": "county",
            "arrest_date": "arrest_date",
            "created_at": "created_at",
            "state": "state",
            "lead_status": "lead_status",
        }
        mongo_sort = sort_map.get(query.sort or "scraped_at", "scraped_at")
        sort_order = -1 if query.order == "desc" else 1
        columns = [
            "full_name", "county", "state", "charges", "bond_amount", "bond_type",
            "lead_score", "lead_status", "status", "booking_number",
            "arrest_date", "booking_date", "court_date", "court_location",
            "case_number", "dob", "sex", "race", "address", "facility", "detail_url",
            "scraped_at",
        ]

        cursor = arrests.find(mongo_query, {"_id": 0}).sort(mongo_sort, sort_order).limit(5000)
        ts = datetime.now().strftime("%Y%m%d_%H%M")

        return StreamingResponse(
            async_csv_streamer(cursor, fieldnames=columns),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=shamrock_leads_{ts}.csv"},
        )
    except Exception as e:
        return {"error": str(e)}


@router.get("/leads/{booking_number}")
async def api_lead_detail(booking_number: str):
    arrests = get_collection("arrests")
    try:
        doc = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not doc:
            try:
                doc = await arrests.find_one({"booking_number": int(booking_number)}, {"_id": 0})
            except (ValueError, TypeError):
                pass
        if not doc:
            return {"error": "Not found"}
        return serialize_doc(doc)
    except Exception as e:
        return {"error": str(e)}


@router.get("/stats")
async def api_overview_stats():
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
    return {
        "total_arrests": total, "counties_active": len(counties), "today_new": today_count,
        "avg_bond": round(bond.get("avg_bond", 0), 2),
        "max_bond": round(bond.get("max_bond", 0), 2),
        "total_bond_value": round(bond.get("total_bond", 0), 2),
        "high_value_leads": high_value,
    }


@router.get("/arrests")
async def api_arrests_list(
    county: str = "", search: str = "", min_bond: str = "",
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    sort: str = "booking_date", dir: int = -1,
):
    arrests = get_collection("arrests")
    query: dict = {}
    if county:
        query["county"] = county
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
            {"case_number": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        query["bond_amount"] = {"$gte": float(min_bond)}
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0, "extra": 0}).sort(sort, dir).skip((page - 1) * limit).limit(limit):
        results.append(serialize_doc(doc))
    return {"arrests": results, "total": total, "page": page,
            "pages": max(1, (total + limit - 1) // limit)}


@router.get("/bond-distribution")
async def api_bond_distribution():
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
        counts[idx_map.get(r["_id"], 9)] = r["count"]
    return {"labels": labels, "counts": counts}


@router.get("/top-charges")
async def api_top_charges():
    arrests = get_collection("arrests")
    results = []
    async for r in arrests.aggregate([
        {"$match": {"charges": {"$exists": True, "$ne": ""}}},
        {"$project": {"words": {"$split": [{"$toUpper": "$charges"}, " | "]}}},
        {"$unwind": "$words"},
        {"$group": {"_id": "$words", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 15},
    ]):
        results.append({"charge": r["_id"], "count": r["count"]})
    return results


@router.get("/bounty-board")
async def api_bounty_board(
    county: str = "", sort: str = "bond_amount", dir: int = -1,
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
):
    arrests = get_collection("arrests")
    query = {"bond_amount": {"$gte": 2500}, "bond_paid": {"$nin": ["YES", "POSTED", "BONDED"]}}
    if county:
        query["county"] = county
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort(sort, dir).skip((page - 1) * limit).limit(limit):
        results.append(serialize_doc(doc))
    return {"targets": results, "total": total, "page": page,
            "pages": max(1, (total + limit - 1) // limit)}


@router.get("/timeline")
async def api_timeline():
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
    return {"dates": dates, "series": {c: list(series[c].values()) for c in counties}}


@router.get("/scraper-health")
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

        # Build county → state lookup from REGISTERED_COUNTIES labels ("Lee (FL)" → "FL")
        import re as _re
        county_state_map: dict[str, str] = {}
        for label in REGISTERED_COUNTIES:
            m = _re.search(r'\(([A-Z]{2})\)$', label)
            if m:
                bare = label[:label.rfind('(')].strip()
                county_state_map[bare] = m.group(1)

        # Process counties that have arrest records
        for county, r in sorted(results_map.items(), key=lambda x: -x[1]["total_records"]):
            if not county:
                continue
            seen.add(county)
            latest = r.get("latest_record") or r.get("latest_scrape")
            if isinstance(latest, datetime) and latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)

            live = live_status.get(county, {})
            cfg = config_map.get(county, {})
            last_run = live.get("last_run")
            if isinstance(last_run, datetime) and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)

            # Determine hours since last run, fallback to latest record age if never run
            if isinstance(last_run, datetime):
                hours_since_run = (now - last_run).total_seconds() / 3600
            else:
                hours_since_run = 999

            if cfg.get("enabled") is False:
                base_status = "disabled"
            elif live.get("status") == "error":
                base_status = "error"
            elif not last_run:
                if isinstance(latest, datetime):
                    hours_since_latest = (now - latest).total_seconds() / 3600
                    base_status = "healthy" if hours_since_latest < 2 else "stale" if hours_since_latest < 6 else "warning" if hours_since_latest < 24 else "offline"
                else:
                    base_status = "never_run"
            else:
                # Scraper ran successfully (status == "ok")
                if hours_since_run < 3:
                    base_status = "healthy"
                elif hours_since_run < 6:
                    base_status = "stale"
                elif hours_since_run < 24:
                    base_status = "warning"
                else:
                    base_status = "offline"

            ui_hours = hours_since_run if last_run else ((now - latest).total_seconds() / 3600 if isinstance(latest, datetime) else 999)

            out.append({
                "county": county,
                "state": county_state_map.get(county, live.get("state", "FL")).upper(),
                "total_records": r["total_records"],
                "in_custody": r["in_custody"],
                "records_24h": counts_24h.get(county, 0),
                "latest_record": latest.isoformat() if isinstance(latest, datetime) else str(latest or ""),
                "last_run": last_run.isoformat() if isinstance(last_run, datetime) else str(last_run or ""),
                "hours_since_update": round(ui_hours, 1),
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
            last_run = live.get("last_run")
            if isinstance(last_run, datetime) and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)

            hours_since_run = (now - last_run).total_seconds() / 3600 if isinstance(last_run, datetime) else 999

            if cfg.get("enabled") is False:
                run_status = "disabled"
            elif live.get("status") == "error":
                run_status = "error"
            elif not last_run:
                run_status = "never_run"
            else:
                if hours_since_run < 3:
                    run_status = "healthy"
                elif hours_since_run < 6:
                    run_status = "stale"
                elif hours_since_run < 24:
                    run_status = "warning"
                else:
                    run_status = "offline"

            # Extract bare county name from "County (ST)" label
            import re as _re2
            m2 = _re2.search(r'\(([A-Z]{2})\)$', county)
            bare_county = county[:county.rfind('(')].strip() if m2 else county
            county_st = m2.group(1) if m2 else "FL"
            out.append({
                "county": bare_county,
                "state": county_st,
                "total_records": 0,
                "in_custody": 0,
                "records_24h": 0,
                "latest_record": "",
                "last_run": last_run.isoformat() if isinstance(last_run, datetime) else str(last_run or ""),
                "hours_since_update": round(hours_since_run, 1),
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

        return out
    except Exception as exc:
        # PII-safe: full traceback may contain query params with names/booking numbers
        logger.error("[stats] api_bond_intelligence error: %s", type(exc).__name__, exc_info=True)
        return {"error": "Internal server error"}


@router.get("/counties")
async def api_counties_stats():
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
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  BOND INTELLIGENCE — Multi-State Bond Analytics
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/bond-intelligence")
async def api_bond_intelligence(
    state: str = Query("", description="Filter by state code: FL, GA, SC, NC"),
    county: str = Query("", description="Filter by county name"),
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
):
    """Comprehensive bond intelligence — totals, averages, distributions, top charges, trends."""
    arrests = get_collection("arrests")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    match_stage: dict = {"$or": [
        {"scraped_at": {"$gte": cutoff.isoformat()}},
        {"scraped_at": {"$gte": cutoff}},
        {"created_at": {"$gte": cutoff.isoformat()}},
    ]}
    if state:
        match_stage["state"] = state.upper()
    if county:
        match_stage["county"] = {"$regex": county, "$options": "i"}
    try:
        summary_result = {}
        async for doc in arrests.aggregate([
            {"$match": match_stage},
            {"$group": {
                "_id": None,
                "total_arrests": {"$sum": 1},
                "total_bond_value": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", 0]}},
                "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
                "max_bond": {"$max": "$bond_amount"},
                "with_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, 1, 0]}},
                "no_bond": {"$sum": {"$cond": [{"$lte": ["$bond_amount", 0]}, 1, 0]}},
                "in_custody": {"$sum": {"$cond": [
                    {"$regexMatch": {"input": {"$ifNull": ["$custody_status", ""]}, "regex": "custody|confined|held|booked", "options": "i"}},
                    1, 0,
                ]}},
            }},
        ]):
            summary_result = doc

        by_state = []
        async for doc in arrests.aggregate([
            {"$match": match_stage},
            {"$group": {
                "_id": "$state",
                "total_arrests": {"$sum": 1},
                "total_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", 0]}},
                "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
                "max_bond": {"$max": "$bond_amount"},
                "with_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, 1, 0]}},
            }},
            {"$sort": {"total_bond": -1}},
        ]):
            by_state.append({
                "state": doc["_id"] or "Unknown",
                "total_arrests": doc["total_arrests"],
                "total_bond": round(doc["total_bond"] or 0, 2),
                "avg_bond": round(doc["avg_bond"] or 0, 2),
                "max_bond": round(doc["max_bond"] or 0, 2),
                "with_bond": doc["with_bond"],
            })

        by_county = []
        async for doc in arrests.aggregate([
            {"$match": match_stage},
            {"$group": {
                "_id": {"county": "$county", "state": "$state"},
                "total_arrests": {"$sum": 1},
                "total_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", 0]}},
                "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}},
                "max_bond": {"$max": "$bond_amount"},
                "with_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, 1, 0]}},
            }},
            {"$sort": {"total_bond": -1}},
            {"$limit": 25},
        ]):
            by_county.append({
                "county": doc["_id"]["county"] or "Unknown",
                "state": doc["_id"]["state"] or "Unknown",
                "total_arrests": doc["total_arrests"],
                "total_bond": round(doc["total_bond"] or 0, 2),
                "avg_bond": round(doc["avg_bond"] or 0, 2),
                "max_bond": round(doc["max_bond"] or 0, 2),
                "with_bond": doc["with_bond"],
            })

        distribution = []
        bucket_labels = {1: "$1–$499", 500: "$500–$999", 1000: "$1K–$2.4K", 2500: "$2.5K–$4.9K",
                         5000: "$5K–$9.9K", 10000: "$10K–$24.9K", 25000: "$25K–$49.9K",
                         50000: "$50K–$99.9K", 100000: "$100K–$499.9K", 500000: "$500K+"}
        async for doc in arrests.aggregate([
            {"$match": {**match_stage, "bond_amount": {"$gt": 0}}},
            {"$bucket": {
                "groupBy": "$bond_amount",
                "boundaries": [1, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 500000, 9999999],
                "default": "Other",
                "output": {"count": {"$sum": 1}, "total": {"$sum": "$bond_amount"}},
            }},
        ]):
            distribution.append({
                "range": bucket_labels.get(doc["_id"], str(doc["_id"])),
                "count": doc["count"],
                "total": round(doc["total"] or 0, 2),
            })

        top_charges = []
        async for doc in arrests.aggregate([
            {"$match": {**match_stage, "bond_amount": {"$gt": 0}}},
            {"$unwind": {"path": "$charges", "preserveNullAndEmptyArrays": False}},
            {"$group": {"_id": "$charges", "count": {"$sum": 1},
                        "total_bond": {"$sum": "$bond_amount"}, "avg_bond": {"$avg": "$bond_amount"}}},
            {"$sort": {"total_bond": -1}},
            {"$limit": 20},
        ]):
            if doc["_id"] and len(str(doc["_id"])) > 2:
                top_charges.append({
                    "charge": str(doc["_id"])[:80], "count": doc["count"],
                    "total_bond": round(doc["total_bond"] or 0, 2),
                    "avg_bond": round(doc["avg_bond"] or 0, 2),
                })

        trend = []
        async for doc in arrests.aggregate([
            {"$match": match_stage},
            {"$addFields": {"date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": {
                "$cond": [{"$eq": [{"$type": "$scraped_at"}, "date"]}, "$scraped_at",
                          {"$toDate": {"$ifNull": ["$scraped_at", "$created_at"]}}]
            }}}}},
            {"$group": {"_id": "$date_str", "arrests": {"$sum": 1},
                        "bond_total": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", 0]}},
                        "avg_bond": {"$avg": {"$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", None]}}}},
            {"$sort": {"_id": 1}},
            {"$limit": 30},
        ]):
            trend.append({"date": doc["_id"], "arrests": doc["arrests"],
                          "bond_total": round(doc["bond_total"] or 0, 2),
                          "avg_bond": round(doc["avg_bond"] or 0, 2)})

        return {
            "summary": {
                "total_arrests": summary_result.get("total_arrests", 0),
                "total_bond_value": round(summary_result.get("total_bond_value", 0), 2),
                "avg_bond": round(summary_result.get("avg_bond") or 0, 2),
                "max_bond": round(summary_result.get("max_bond") or 0, 2),
                "with_bond": summary_result.get("with_bond", 0),
                "no_bond": summary_result.get("no_bond", 0),
                "in_custody": summary_result.get("in_custody", 0),
                "bond_capture_rate": round(
                    (summary_result.get("with_bond", 0) / max(summary_result.get("total_arrests", 1), 1)) * 100, 1),
            },
            "by_state": by_state, "by_county": by_county,
            "distribution": distribution, "top_charges": top_charges, "trend": trend,
            "filters": {"state": state, "county": county, "days": days},
        }
    except Exception as exc:
        # PII-safe: full traceback may contain query params with names/booking numbers
        logger.error("[stats] api_arrests_multistate_stats error: %s", type(exc).__name__, exc_info=True)
        return {"error": "Internal server error"}


@router.get("/arrests/recent")
async def api_arrests_recent(
    limit: int = Query(50, ge=1, le=200),
    state: str = Query("", description="Filter by state: FL, GA, SC, NC"),
    county: str = Query("", description="Filter by county"),
    min_bond: float = Query(0, ge=0),
    hours: int = Query(24, ge=1, le=168),
):
    """Live recent arrests feed — newest first, multi-state aware."""
    arrests = get_collection("arrests")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    query: dict = {"$or": [
        {"scraped_at": {"$gte": cutoff.isoformat()}},
        {"scraped_at": {"$gte": cutoff}},
        {"created_at": {"$gte": cutoff.isoformat()}},
    ]}
    if state:
        query["state"] = state.upper()
    if county:
        query["county"] = {"$regex": county, "$options": "i"}
    if min_bond > 0:
        query["bond_amount"] = {"$gte": min_bond}
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort("scraped_at", -1).limit(limit):
        results.append(serialize_doc(doc))
    return {"arrests": results, "total": total, "hours": hours,
            "filters": {"state": state, "county": county, "min_bond": min_bond}}


@router.get("/arrests/stats/multi-state")
async def api_arrests_multistate_stats():
    """High-level arrest stats by state — for Command Center KPI cards."""
    arrests = get_collection("arrests")
    now = datetime.now(timezone.utc)
    h24 = now - timedelta(hours=24)
    h168 = now - timedelta(hours=168)
    try:
        states: dict = {}
        async for doc in arrests.aggregate([{"$group": {"_id": "$state", "total": {"$sum": 1}}}]):
            s = doc["_id"] or "Unknown"
            states.setdefault(s, {})["total"] = doc["total"]
        async for doc in arrests.aggregate([
            {"$match": {"$or": [{"scraped_at": {"$gte": h24.isoformat()}}, {"scraped_at": {"$gte": h24}}]}},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]):
            states.setdefault(doc["_id"] or "Unknown", {})["last_24h"] = doc["count"]
        async for doc in arrests.aggregate([
            {"$match": {"$or": [{"scraped_at": {"$gte": h168.isoformat()}}, {"scraped_at": {"$gte": h168}}]}},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]):
            states.setdefault(doc["_id"] or "Unknown", {})["last_7d"] = doc["count"]
        async for doc in arrests.aggregate([
            {"$match": {"bond_amount": {"$gt": 0}}},
            {"$group": {"_id": "$state", "total_bond": {"$sum": "$bond_amount"},
                        "avg_bond": {"$avg": "$bond_amount"}, "max_bond": {"$max": "$bond_amount"}}},
        ]):
            s = doc["_id"] or "Unknown"
            states.setdefault(s, {}).update({
                "total_bond": round(doc["total_bond"] or 0, 2),
                "avg_bond": round(doc["avg_bond"] or 0, 2),
                "max_bond": round(doc["max_bond"] or 0, 2),
            })
        async for doc in arrests.aggregate([
            {"$match": {"lead_score": {"$gte": 70}}},
            {"$group": {"_id": "$state", "hot": {"$sum": 1}}},
        ]):
            states.setdefault(doc["_id"] or "Unknown", {})["hot_leads"] = doc["hot"]

        out = [{"state": s, "total": d.get("total", 0), "last_24h": d.get("last_24h", 0),
                "last_7d": d.get("last_7d", 0), "total_bond": d.get("total_bond", 0),
                "avg_bond": d.get("avg_bond", 0), "max_bond": d.get("max_bond", 0),
                "hot_leads": d.get("hot_leads", 0)} for s, d in sorted(states.items())]

        return {
            "by_state": out,
            "totals": {
                "all_time": sum(s["total"] for s in out),
                "last_24h": sum(s["last_24h"] for s in out),
                "total_bond_value": round(sum(s["total_bond"] for s in out), 2),
                "states_active": len(out),
            },
        }
    except Exception as exc:
        # PII-safe: full traceback may contain query params with names/booking numbers
        logger.error("[stats] error: %s", type(exc).__name__, exc_info=True)
        return {"error": "Internal server error"}
