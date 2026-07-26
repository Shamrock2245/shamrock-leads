"""
Collateral API Blueprint — ShamrockLeads
CRUD endpoints for physical collateral items held in agency vault & return receipt generation.
"""

from fastapi import APIRouter, Request, Query, Path
from starlette.responses import Response
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any

from dashboard.services.collateral_service import (
    add_collateral_item,
    list_collateral_items,
    return_collateral_item,
    generate_collateral_receipt_pdf
)

collateral_bp = APIRouter(prefix="/api/collateral", tags=["collateral"])

@collateral_bp.get("")
async def get_collateral_list(
    booking_number: Optional[str] = Query(None),
    status: Optional[str] = Query(None)
):
    """List collateral items in vault."""
    try:
        items = await list_collateral_items(booking_number=booking_number, status=status)
        return JSONResponse(status_code=200, content={"success": True, "count": len(items), "items": items})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@collateral_bp.post("/add")
async def create_collateral_item(request: Request):
    """Record a new collateral item in vault."""
    try:
        data = await request.json() or {}
        item = await add_collateral_item(data)
        return JSONResponse(status_code=200, content={"success": True, "item": item})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@collateral_bp.post("/return/{collateral_id}")
async def return_collateral(
    collateral_id: str = Path(...),
    request: Request = None,
):
    """Mark collateral item as returned to depositor."""
    try:
        data = await request.json()
        returned_by = data.get("returned_by", "Staff")
        return_note = data.get("return_note", "")
        item = await return_collateral_item(collateral_id, returned_by=returned_by, return_note=return_note)
        return JSONResponse(status_code=200, content={"success": True, "message": "Collateral marked as returned", "item": item})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@collateral_bp.get("/receipt-pdf/{collateral_id}")
async def download_collateral_receipt(collateral_id: str = Path(...)):
    """Generate printable PDF Collateral Return Receipt."""
    try:
        items = await list_collateral_items()
        item = next((i for i in items if i.get("collateral_id") == collateral_id), None)
        if not item:
            return JSONResponse(status_code=404, content={"error": "Collateral item not found"})

        pdf_bytes = generate_collateral_receipt_pdf(item)
        filename = f"Collateral_Receipt_{item.get('tag_number', collateral_id)}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"PDF generation failed: {str(exc)}"})
