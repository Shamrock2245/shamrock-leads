from __future__ import annotations
"""Stats Router — FastAPI port of api/stats.py (13 endpoints)"""
import csv
import io
import re as re_mod
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
from dashboard.deps import get_collection
from dashboard.extensions import REGISTERED_COUNTIES
from dashboard.routers.helpers import serialize_doc, async_csv_streamer
from dashboard.models.leads import LeadsQueryModel

router = APIRouter(prefix="/api", tags=["stats"])


def _build_leads_query(query: LeadsQueryModel):
    q: dict = {}
    if query.status:
        q["lead_status"] = query.status
    if query.county:
        cs = [c.strip() for c in query.county.split(",") if c.strip()]
        q["county"] = cs[0] if len(cs) == 1 else {"$in": cs}
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
        pat = re_mod.compile(re_mod.escape(query.search), re_mod.IGNORECASE)
        sor = [
            {"full_name": {"$regex": pat}}, {"charges": {"$regex": pat}},
            {"booking_number": {"$regex": pat}}, {"case_number": {"$regex": pat}},
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
            {"_id": 0, "full_name": 1, "county": 1, "charges": 1,
             "bond_amount": 1, "lead_score": 1, "lead_status": 1,
             "status": 1, "booking_number": 1, "dob": 1, "arrest_date": 1,
             "booking_date": 1, "bond_type": 1, "detail_url": 1},
        ).sort("bond_amount", -1).limit(25):
            bond_ready.append(serialize_doc(doc))
        pipeline_total = sum(d.get("bond_amount", 0) for d in bond_ready)
        premium_est = sum(max(100, d.get("bond_amount", 0) * 0.1) for d in bond_ready)
        recent = []
        async for doc in arrests.find(
            {}, {"_id": 0, "full_name": 1, "county": 1, "bond_amount": 1,
                  "lead_score": 1, "lead_status": 1, "scraped_at": 1,
                  "status": 1, "charges": 1},
        ).sort("scraped_at", -1).limit(10):
            recent.append(serialize_doc(doc))
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
        return {
            "bond_ready": bond_ready, "pipeline_total": pipeline_total,
            "premium_estimate": premium_est, "bond_ready_count": len(bond_ready),
            "recent_activity": recent, "custody_by_county": custody_by_county,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/leads")
async def api_leads(
    query: LeadsQueryModel = Depends(),
):
    arrests = get_collection("arrests")
    try:
        mongo_query = _build_leads_query(query)
        sort_map = {
            "lead_score": "lead_score", "bond_amount": "bond_amount",
            "booking_date": "booking_date", "full_name": "full_name",
            "county": "county", "arrest_date": "arrest_date", "created_at": "created_at",
        }
        mongo_sort = sort_map.get(query.sort, "lead_score")
        sort_order = -1 if query.order == "desc" else 1
        skip = (query.page - 1) * query.limit
        projection = {
            "_id": 0, "full_name": 1, "first_name": 1, "last_name": 1,
            "booking_number": 1, "county": 1, "charges": 1, "bond_amount": 1,
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
        return {
            "leads": leads_list, "total": total, "page": query.page, "limit": query.limit,
            "pages": max(1, (total + query.limit - 1) // query.limit),
            "counties": counties_list,
            "query": {"status": query.status, "county": query.county, "search": query.search,
                      "sort": query.sort, "order": query.order},
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
            "lead_score": "lead_score", "bond_amount": "bond_amount",
            "booking_date": "booking_date", "full_name": "full_name",
            "county": "county", "arrest_date": "arrest_date",
        }
        mongo_sort = sort_map.get(query.sort, "lead_score")
        sort_order = -1 if query.order == "desc" else 1
        columns = [
            "full_name", "county", "charges", "bond_amount", "bond_type",
            "lead_score", "lead_status", "status", "booking_number",
            "arrest_date", "booking_date", "court_date", "court_location",
            "case_number", "dob", "sex", "race", "address", "facility", "detail_url",
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
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
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
