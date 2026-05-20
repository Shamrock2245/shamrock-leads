from __future__ import annotations
"""Leads Router — FastAPI port of api/leads.py (legacy leads endpoints)"""
import csv
import io
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from dashboard.deps import get_collection
from dashboard.routers.helpers import serialize_doc, async_csv_streamer

router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/leads-legacy")
async def api_leads_legacy(
    county: str = "", status: str = "", days: int = 0,
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    sort: str = "scraped_at", dir: int = -1,
    search: str = "", min_bond: str = "", has_indemnitor: str = "",
):
    arrests = get_collection("arrests")
    query: dict = {}
    if county:
        query["county"] = county
    if status:
        query["lead_status"] = status
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["scraped_at"] = {"$gte": cutoff}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass

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
            query["booking_number"] = {"$in": list(ind_bookings)}
        else:
            return {"leads": [], "total": 0, "page": 1, "pages": 1}

    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort(sort, dir).skip((page - 1) * limit).limit(limit):
        results.append(serialize_doc(doc))
    return {"leads": results, "total": total, "page": page,
            "pages": max(1, (total + limit - 1) // limit)}


@router.get("/leads-legacy/export")
async def api_leads_legacy_export(
    county: str = "", status: str = "", days: int = 0,
):
    arrests = get_collection("arrests")
    query: dict = {}
    if county:
        query["county"] = county
    if status:
        query["lead_status"] = status
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["scraped_at"] = {"$gte": cutoff}

    columns = [
        "full_name", "county", "charges", "bond_amount", "bond_type",
        "lead_score", "lead_status", "status", "booking_number",
        "arrest_date", "booking_date", "court_date", "court_location",
        "case_number", "dob", "sex", "race", "address", "facility", "detail_url",
        "scraped_at", "updated_at"
    ]

    cursor = arrests.find(query, {"_id": 0}).sort("scraped_at", -1).limit(5000)
    
    return StreamingResponse(
        async_csv_streamer(cursor, fieldnames=columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=shamrock_leads_export.csv"},
    )
