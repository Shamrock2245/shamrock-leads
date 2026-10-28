"""Contacts API — OSINT contact discovery for defendants."""
from __future__ import annotations

import logging

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection, get_db
from dashboard.services.contact_discovery import ContactDiscoveryService, contact_identity

logger = logging.getLogger(__name__)

contacts_bp = APIRouter(prefix="/api", tags=["contacts"])


@contacts_bp.post("/contacts/discover")
async def discover_contacts(request: Request):
    """Run OSINT contact discovery for a defendant."""
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number")
        full_name = data.get("full_name")
        county = data.get("county")
        state = data.get("state")
        address = data.get("address")

        if not booking_number:
            return JSONResponse(
                {"error": "booking_number_required"},
                status_code=400,
            )

        service = ContactDiscoveryService(get_db())
        result = await service.discover(
            booking_number=booking_number,
            full_name=full_name,
            county=county,
            state=state,
            address=address,
        )
        if result.get("discovery_status") == "identity_ambiguous":
            return JSONResponse(
                {"error": "county_and_state_required", "discovery_status": "identity_ambiguous"},
                status_code=409,
            )
        return result
    except Exception as exc:
        logger.warning("contact discover failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)


@contacts_bp.get("/contacts/{booking_number}")
async def get_contacts(booking_number: str, county: Optional[str] = None, state: Optional[str] = None):
    """Get discovered contacts for one arrest (state + county + booking).

    Without county/state this answers only when exactly one stored doc has
    the booking number; two counties with the same booking return 409.
    """
    empty = {
        "contacts": [],
        "candidates": [],
        "discovered_contacts": [],
        "discovery_status": "not_found",
        "status": "not_found",
    }
    try:
        contacts_col = get_collection("contacts")
        identity = contact_identity(state, county, booking_number)
        if identity:
            doc = await contacts_col.find_one(identity, {"_id": 0})
        else:
            query = {"booking_number": booking_number}
            if county:
                query["county"] = county
            if state:
                query["state"] = str(state).strip().upper()
            rows = await contacts_col.find(query, {"_id": 0}).to_list(length=2)
            if len(rows) > 1:
                return JSONResponse(
                    {**empty, "error": "county_and_state_required", "discovery_status": "identity_ambiguous"},
                    status_code=409,
                )
            doc = rows[0] if rows else None
        if not doc:
            return empty
        contacts = list(doc.get("discovered_contacts") or [])
        doc["contacts"] = contacts
        doc["candidates"] = contacts
        doc["discovery_status"] = doc.get("discovery_status") or "not_found"
        return doc
    except Exception as exc:
        logger.warning("contact read failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)
