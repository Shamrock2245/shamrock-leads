"""Contacts API — OSINT contact discovery for defendants."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection, get_db
from dashboard.services.contact_discovery import ContactDiscoveryService

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
            address=address,
        )
        return result
    except Exception as exc:
        logger.warning("contact discover failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)


@contacts_bp.get("/contacts/{booking_number}")
async def get_contacts(booking_number: str):
    """Get discovered contacts for a defendant."""
    try:
        contacts_col = get_collection("contacts")
        doc = await contacts_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not doc:
            return {
                "contacts": [],
                "candidates": [],
                "discovered_contacts": [],
                "discovery_status": "not_found",
                "status": "not_found",
            }
        contacts = list(doc.get("discovered_contacts") or [])
        doc["contacts"] = contacts
        doc["candidates"] = contacts
        doc["discovery_status"] = doc.get("discovery_status") or "not_found"
        return doc
    except Exception as exc:
        logger.warning("contact read failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)
