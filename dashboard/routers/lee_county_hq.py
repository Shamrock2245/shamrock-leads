"""
ShamrockLeads — Lee County HQ API Blueprint
REST endpoints powering the dedicated Lee County Master Command Center.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db, get_collection
from core.lee_county_master import (
    get_lee_county_overview,
    get_lee_county_leads,
    find_family_contacts,
    send_lee_county_outreach,
    run_lee_county_autopilot_sweep,
    calculate_make_it_work_terms,
    generate_family_outreach_message,
)

logger = logging.getLogger(__name__)

lee_county_hq_bp = APIRouter(prefix="/api/lee-county", tags=["lee_county_hq"])


@lee_county_hq_bp.get("/overview")
async def api_lee_county_overview():
    """Retrieve top-level KPI metrics, live facility status, and autopilot config for Lee County."""
    try:
        db = get_db()
        data = await get_lee_county_overview(db)
        return data
    except Exception as exc:
        logger.error(f"❌ Failed to fetch Lee County overview: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.get("/leads")
async def api_lee_county_leads(
    filter_type: str = Query("all", alias="filter"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    search: str = Query(""),
):
    """Retrieve filterable, scored Lee County leads with deal calculations."""
    try:
        db = get_db()
        data = await get_lee_county_leads(
            db,
            filter_type=filter_type,
            limit=limit,
            skip=skip,
            search=search,
        )
        return data
    except Exception as exc:
        logger.error(f"❌ Failed to query Lee County leads: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.get("/defendant/{booking_number}/family")
async def api_get_family_contacts(
    booking_number: str,
    name: str = Query(""),
    phone: str = Query(""),
):
    """Discover family members, cosigners, and emergency contacts for a Lee County defendant."""
    try:
        db = get_db()
        contacts = await find_family_contacts(
            db,
            defendant_name=name,
            booking_number=booking_number,
            defendant_phone=phone,
        )
        return {"ok": True, "booking_number": booking_number, "contacts": contacts}
    except Exception as exc:
        logger.error(f"❌ Failed to discover family contacts for {booking_number}: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.post("/outreach/send")
async def api_send_outreach(request: Request):
    """
    1-Click manual outreach dispatch to a family member / cosigner.
    Body:
      {
        "booking_number": "2026-12345",
        "recipient_phone": "+12395551234",
        "recipient_name": "Jane Doe",
        "custom_message": "Optional custom text"
      }
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    booking_number = (data.get("booking_number") or "").strip()
    recipient_phone = (data.get("recipient_phone") or "").strip()
    recipient_name = (data.get("recipient_name") or "").strip()
    custom_message = data.get("custom_message")

    if not booking_number or not recipient_phone:
        return JSONResponse({"ok": False, "error": "booking_number and recipient_phone are required"}, status_code=400)

    try:
        db = get_db()
        res = await send_lee_county_outreach(
            db=db,
            booking_number=booking_number,
            recipient_phone=recipient_phone,
            recipient_name=recipient_name,
            custom_message=custom_message,
            triggered_by="dashboard_1click",
        )
        status_code = 200 if res.get("ok") else 400
        return JSONResponse(res, status_code=status_code)
    except Exception as exc:
        logger.error(f"❌ Error sending outreach to {recipient_phone}: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.get("/autopilot/config")
async def api_get_autopilot_config():
    """Get the current Auto-Pilot configuration for Lee County."""
    try:
        col = get_collection("lee_county_config")
        cfg = await col.find_one({"county": "Lee"}, {"_id": 0}) or {
            "county": "Lee",
            "autopilot_enabled": False,
            "min_score": 70,
            "min_bond": 500,
            "quiet_hours": "23:00-06:30",
            "daily_budget_texts": 100,
            "emergency_bypass": True,
        }
        return {"ok": True, "config": cfg}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.post("/autopilot/config")
async def api_update_autopilot_config(request: Request):
    """
    Update Auto-Pilot settings for Lee County.
    Body:
      {
        "autopilot_enabled": true/false,
        "min_score": 70,
        "min_bond": 500,
        "quiet_hours": "23:00-06:30",
        "emergency_bypass": true
      }
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    try:
        col = get_collection("lee_county_config")
        update_doc: Dict[str, Any] = {
            "county": "Lee",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if "autopilot_enabled" in data:
            update_doc["autopilot_enabled"] = bool(data["autopilot_enabled"])
        if "min_score" in data:
            update_doc["min_score"] = int(data["min_score"])
        if "min_bond" in data:
            update_doc["min_bond"] = float(data["min_bond"])
        if "quiet_hours" in data:
            update_doc["quiet_hours"] = str(data["quiet_hours"])
        if "emergency_bypass" in data:
            update_doc["emergency_bypass"] = bool(data["emergency_bypass"])

        await col.update_one(
            {"county": "Lee"},
            {"$set": update_doc},
            upsert=True,
        )

        return {"ok": True, "message": "Lee County Auto-Pilot configuration updated", "config": update_doc}
    except Exception as exc:
        logger.error(f"❌ Failed to update Lee County autopilot config: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.post("/autopilot/sweep")
async def api_trigger_autopilot_sweep():
    """Trigger an immediate Auto-Pilot evaluation and dispatch sweep."""
    try:
        db = get_db()
        result = await run_lee_county_autopilot_sweep(db)
        return result
    except Exception as exc:
        logger.error(f"❌ Error during Lee County autopilot sweep: {exc}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@lee_county_hq_bp.get("/calculator")
async def api_calculate_terms(bond: float = Query(0.0, ge=0.0)):
    """Calculate 'Make It Work' payment terms and statutory premium."""
    terms = calculate_make_it_work_terms(bond)
    return {"ok": True, "terms": terms}
