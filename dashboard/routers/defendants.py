from __future__ import annotations
"""Defendants Router — FastAPI port of api/defendants.py (9 endpoints)"""
import logging
from datetime import datetime
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.deps import get_collection, get_db
from dashboard.services.defendant_normalizer import (
    DefendantNormalizationService, normalize_name_part, normalize_dob,
)
from dashboard.routers.helpers import serialize_doc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["defendants"])


def _get_svc() -> DefendantNormalizationService:
    return DefendantNormalizationService(get_db())


@router.get("/defendants")
async def api_defendants(
    county: str = "", search: str = "",
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
    min_arrests: int = 0, sort: str = "bond_amount", dir: int = -1,
    min_bond: str = "",
):
    svc = _get_svc()
    defendants_col = get_collection("defendants")
    total_defendants = await defendants_col.estimated_document_count()

    if total_defendants > 0:
        result = await svc.search_defendants(
            query_str=search, county=county, page=page,
            limit=limit, min_arrests=min_arrests,
        )
        return result

    # Bootstrap fallback
    arrests = get_collection("arrests")
    query: dict = {}
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
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass
    total = await arrests.count_documents(query)
    results = []
    async for doc in arrests.find(query, {"_id": 0}).sort(sort, dir).skip((page - 1) * limit).limit(limit):
        results.append(serialize_doc(doc))
    return {
        "defendants": results, "total": total, "page": page,
        "pages": max(1, (total + limit - 1) // limit),
        "source": "arrests_fallback",
        "note": "Run POST /api/defendants/normalize/batch to build the defendants collection.",
    }


@router.get("/defendants/stats")
async def defendants_stats():
    try:
        defendants_col = get_collection("defendants")
        arrests_col = get_collection("arrests")
        total_defendants = await defendants_col.count_documents({"active": {"$ne": False}})
        total_arrests = await arrests_col.estimated_document_count()
        linked_arrests = await arrests_col.count_documents({"defendant_id": {"$exists": True}})
        unlinked_arrests = total_arrests - linked_arrests
        repeat_offenders = await defendants_col.count_documents(
            {"total_arrests": {"$gte": 2}, "active": {"$ne": False}}
        )
        top_counties = []
        async for row in defendants_col.aggregate([
            {"$match": {"active": {"$ne": False}}},
            {"$unwind": "$counties"},
            {"$group": {"_id": "$counties", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}, {"$limit": 10},
        ]):
            top_counties.append({"county": row["_id"], "defendants": row["count"]})
        return {
            "total_defendants": total_defendants, "total_arrests": total_arrests,
            "linked_arrests": linked_arrests, "unlinked_arrests": unlinked_arrests,
            "repeat_offenders": repeat_offenders,
            "normalization_coverage_pct": (
                round(linked_arrests / total_arrests * 100, 1) if total_arrests else 0
            ),
            "top_counties": top_counties,
        }
    except Exception as exc:
        logger.exception("defendants_stats error")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/defendants/lookup")
async def lookup_defendant(last_name: str = "", first_name: str = "", dob: str = ""):
    try:
        if not last_name or not first_name:
            return JSONResponse({"error": "last_name and first_name are required"}, 400)
        from dashboard.services.defendant_normalizer import make_identity_key
        identity_key = make_identity_key(last_name, first_name, dob)
        defendants_col = get_collection("defendants")
        doc = await defendants_col.find_one(
            {"identity_key": identity_key, "active": {"$ne": False}}, {"_id": 0},
        )
        if doc:
            return {"found": True, "defendant": doc, "match_type": "exact"}
        svc = _get_svc()
        norm = {
            "first_name": first_name.strip().title(),
            "last_name": last_name.strip().title(),
            "norm_first": normalize_name_part(first_name),
            "norm_last": normalize_name_part(last_name),
            "dob": normalize_dob(dob),
        }
        fuzzy = await svc._fuzzy_lookup(norm)
        if fuzzy:
            return {"found": True, "defendant": fuzzy, "match_type": "fuzzy"}
        return {"found": False, "identity_key": identity_key}
    except Exception as exc:
        logger.exception("lookup_defendant error")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/defendants/{defendant_id}")
async def get_defendant(defendant_id: str):
    svc = _get_svc()
    doc = await svc.get_defendant(defendant_id)
    if not doc:
        return JSONResponse({"error": "Defendant not found", "defendant_id": defendant_id}, 404)
    return doc


@router.get("/defendants/{defendant_id}/arrests")
async def get_defendant_arrests(defendant_id: str):
    svc = _get_svc()
    arrests = await svc.get_defendant_arrests(defendant_id)
    return {"defendant_id": defendant_id, "arrests": arrests, "total": len(arrests)}


@router.post("/defendants/normalize")
async def normalize_single(request: Request):
    try:
        data = await request.json()
        if not data:
            return JSONResponse({"error": "Request body required"}, 400)
        svc = _get_svc()
        if "arrest_doc" in data:
            arrest_doc = data["arrest_doc"]
        else:
            county = (data.get("county") or "").strip()
            booking_number = (data.get("booking_number") or "").strip()
            if not county or not booking_number:
                return JSONResponse({"error": "Provide 'arrest_doc' OR both 'county' and 'booking_number'"}, 400)
            arrests_col = get_collection("arrests")
            arrest_doc = await arrests_col.find_one(
                {"county": county, "booking_number": booking_number}, {"_id": 0},
            )
            if not arrest_doc:
                return JSONResponse({"error": f"Arrest not found: {county}/{booking_number}"}, 404)
        result = await svc.normalize_arrest(arrest_doc)
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("normalize_single error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/defendants/normalize/batch")
async def normalize_batch(request: Request):
    try:
        data = (await request.json()) or {}
        county = data.get("county") or None
        limit = min(int(data.get("limit", 500)), 5000)
        svc = _get_svc()
        result = await svc.normalize_batch(county=county, limit=limit)
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("normalize_batch error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.patch("/defendants/{defendant_id}/contact")
async def update_contact(defendant_id: str, request: Request):
    try:
        data = (await request.json()) or {}
        svc = _get_svc()
        updated = await svc.update_defendant_contact(
            defendant_id=defendant_id,
            phone=data.get("phone"), email=data.get("email"),
            address=data.get("address"), agent=data.get("agent", "dashboard"),
        )
        if updated:
            return {"success": True, "defendant_id": defendant_id}
        return JSONResponse({"success": False, "error": "Defendant not found or no changes"}, 404)
    except Exception as exc:
        logger.exception("update_contact error for %s", defendant_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/defendants/merge")
async def merge_defendants(request: Request):
    try:
        data = (await request.json()) or {}
        primary_id = (data.get("primary_id") or "").strip()
        secondary_id = (data.get("secondary_id") or "").strip()
        agent = data.get("agent", "dashboard")
        if not primary_id or not secondary_id:
            return JSONResponse({"error": "Both primary_id and secondary_id are required"}, 400)
        if primary_id == secondary_id:
            return JSONResponse({"error": "primary_id and secondary_id must be different"}, 400)
        svc = _get_svc()
        result = await svc.merge_defendants(
            primary_id=primary_id, secondary_id=secondary_id, agent=agent,
        )
        return result
    except Exception as exc:
        logger.exception("merge_defendants error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
