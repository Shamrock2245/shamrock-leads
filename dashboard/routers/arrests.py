"""ShamrockLeads — Arrests Router (FastAPI port of api/arrests.py)"""
from datetime import datetime
from fastapi import APIRouter, Query
from dashboard.deps import get_collection
from dashboard.extensions import (
    REGISTERED_COUNTIES,
    county_label,
    parse_registered_county,
)
from dashboard.routers.helpers import serialize_doc

router = APIRouter(prefix="/api", tags=["arrests"])


@router.get("/counties-detail")
async def api_counties():
    arrests = get_collection("arrests")
    pipeline = [
        {"$group": {
            "_id": {
                "county": "$county",
                "state": {"$toUpper": {"$ifNull": ["$state", "FL"]}},
            },
            "total": {"$sum": 1},
            "latest": {"$max": "$scraped_at"},
        }},
        {"$sort": {"_id.county": 1}},
    ]
    # Key by County (ST) so bare Mongo names map onto registry labels
    county_data: dict[str, dict] = {}
    async for row in arrests.aggregate(pipeline):
        grp = row.get("_id") or {}
        bare_raw = (grp.get("county") or "").strip()
        if not bare_raw:
            continue
        bare, st_from_label = parse_registered_county(bare_raw)
        st = (grp.get("state") or st_from_label or "FL").upper()
        label = county_label(bare, st)
        existing = county_data.get(label)
        if existing:
            existing["total"] = existing.get("total", 0) + row.get("total", 0)
            # Keep the newer latest if both present
            if row.get("latest") and (
                not existing.get("latest") or row["latest"] > existing["latest"]
            ):
                existing["latest"] = row["latest"]
        else:
            county_data[label] = {
                "total": row.get("total", 0),
                "latest": row.get("latest"),
            }

    result = []
    seen: set[str] = set()
    for name in REGISTERED_COUNTIES:
        d = county_data.get(name, {})
        seen.add(name)
        result.append({
            "county": name,
            "total": d.get("total", 0),
            "latest": d.get("latest"),
            "active": d.get("total", 0) > 0,
        })
    for name, d in sorted(county_data.items()):
        if name in seen:
            continue
        result.append({
            "county": name,
            "total": d.get("total", 0),
            "latest": d.get("latest"),
            "active": True,
        })
    return {"counties": result}


@router.get("/counties/{county_name}/arrests")
async def api_county_arrests(
    county_name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    sort: str = "created_at",
    dir: int = -1,
    search: str = "",
):
    arrests = get_collection("arrests")
    query: dict = {"county": county_name}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
        ]
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort(sort, dir).skip((page - 1) * limit).limit(limit):
        results.append(serialize_doc(doc))
    return {"county": county_name, "arrests": results, "total": total,
            "page": page, "pages": max(1, (total + limit - 1) // limit)}


@router.get("/arrests/search")
async def api_arrests_search(
    q: str = Query("", min_length=2),
    county: str = "",
    limit: int = Query(20, ge=1, le=50),
    status: str = "",
):
    arrests = get_collection("arrests")
    query: dict = {"$or": [
        {"full_name": {"$regex": q, "$options": "i"}},
        {"booking_number": {"$regex": q, "$options": "i"}},
        {"case_number": {"$regex": q, "$options": "i"}},
        {"charges": {"$regex": q, "$options": "i"}},
    ]}
    if county:
        query["county"] = county
    if status:
        query["custody_status"] = {"$regex": status, "$options": "i"}
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort("scraped_at", -1).limit(limit):
        results.append(serialize_doc(doc))
    return {"arrests": results, "total": total, "query": q}


@router.get("/arrests/by-booking/{booking_number}")
async def api_arrest_by_booking(booking_number: str):
    arrests = get_collection("arrests")
    doc = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
    if not doc:
        doc = await arrests.find_one(
            {"booking_number": {"$regex": f"^{booking_number}$", "$options": "i"}}, {"_id": 0})
    if not doc:
        return {"found": False, "error": f"No arrest found for booking {booking_number}"}
    return {"found": True, "arrest": serialize_doc(doc)}
