from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
from dashboard.deps import get_collection
from dashboard.routers.helpers import serialize_doc, async_csv_streamer
from dashboard.models.leads import LeadsQueryModel

router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/leads-legacy")
async def api_leads_legacy(
    has_indemnitor: str = "",
    query: LeadsQueryModel = Depends(),
):
    arrests = get_collection("arrests")
    mongo_query: dict = {}
    if query.county:
        mongo_query["county"] = query.county
    if query.status:
        mongo_query["lead_status"] = query.status
    if query.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=query.days)).isoformat()
        mongo_query["scraped_at"] = {"$gte": cutoff}
    if query.search:
        mongo_query["$or"] = [
            {"full_name": {"$regex": query.search, "$options": "i"}},
            {"first_name": {"$regex": query.search, "$options": "i"}},
            {"last_name": {"$regex": query.search, "$options": "i"}},
            {"charges": {"$regex": query.search, "$options": "i"}},
            {"booking_number": {"$regex": query.search, "$options": "i"}},
            {"case_number": {"$regex": query.search, "$options": "i"}},
            {"address": {"$regex": query.search, "$options": "i"}},
        ]
    if query.min_bond is not None:
        mongo_query["bond_amount"] = {"$gte": query.min_bond}

    if has_indemnitor.lower() == "true":
        active = get_collection("active_bonds")
        prospective = get_collection("prospective_bonds")
        ind_filter = {"$or": [
            {"indemnitor.phone": {"$exists": True, "$ne": ""}},
            {"indemnitor_phone": {"$exists": True, "$ne": ""}},
            {"indemnitors.0": {"$exists": True}},
        ]}
        ind_bookings = set()
        async for doc in active.find(ind_filter, {"booking_number": 1}):
            if doc.get("booking_number"):
                ind_bookings.add(doc["booking_number"])
        async for doc in prospective.find(ind_filter, {"booking_number": 1}):
            if doc.get("booking_number"):
                ind_bookings.add(doc["booking_number"])
        if ind_bookings:
            mongo_query["booking_number"] = {"$in": list(ind_bookings)}
        else:
            return {"leads": [], "total": 0, "page": 1, "pages": 1}

    mongo_sort = query.sort if query.sort else "scraped_at"
    mongo_dir = -1 if query.order == "desc" else 1

    total = await arrests.count_documents(mongo_query)
    results = []
    async for doc in arrests.find(mongo_query, {"_id": 0}).sort(mongo_sort, mongo_dir).skip((query.page - 1) * query.limit).limit(query.limit):
        results.append(serialize_doc(doc))
    return {"leads": results, "total": total, "page": query.page,
            "pages": max(1, (total + query.limit - 1) // query.limit)}


@router.get("/leads-legacy/export")
async def api_leads_legacy_export(
    query: LeadsQueryModel = Depends(),
):
    arrests = get_collection("arrests")
    mongo_query: dict = {}
    if query.county:
        mongo_query["county"] = query.county
    if query.status:
        mongo_query["lead_status"] = query.status
    if query.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=query.days)).isoformat()
        mongo_query["scraped_at"] = {"$gte": cutoff}

    columns = [
        "full_name", "county", "charges", "bond_amount", "bond_type",
        "lead_score", "lead_status", "status", "booking_number",
        "arrest_date", "booking_date", "court_date", "court_location",
        "case_number", "dob", "sex", "race", "address", "facility", "detail_url",
        "scraped_at", "updated_at"
    ]

    cursor = arrests.find(mongo_query, {"_id": 0}).sort("scraped_at", -1).limit(5000)
    
    return StreamingResponse(
        async_csv_streamer(cursor, fieldnames=columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=shamrock_leads_export.csv"},
    )
