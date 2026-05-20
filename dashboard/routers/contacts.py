from fastapi import APIRouter, Request
"""Contacts API Blueprint (Phase 2) — contact discovery"""
from dashboard.extensions import get_collection
from dashboard.services.contact_discovery import ContactDiscoveryService

contacts_bp = APIRouter(prefix="/api", tags=["contacts"])
@contacts_bp.post("/contacts/discover")
async def discover_contacts(request: Request):
    """Run OSINT contact discovery for a defendant."""
    try:
        data = await request.json()
        booking_number = data.get("booking_number")
        full_name = data.get("full_name")
        county = data.get("county")
        address = data.get("address")

        if not booking_number or not full_name:
            return JSONResponse({"error": "Missing required fields (booking_number, full_name)"}, status_code=400)

        service = ContactDiscoveryService(get_db())
        result = await service.discover(
            booking_number=booking_number,
            full_name=full_name,
            county=county,
            address=address,
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@contacts_bp.get("/contacts/{booking_number}")
async def get_contacts(booking_number):
    """Get discovered contacts for a defendant."""
    try:
        contacts_col = get_collection("contacts")
        doc = await contacts_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not doc:
            return {"contacts": [], "status": "not_found"}
        return doc
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
