"""
Collateral API Blueprint — ShamrockLeads
CRUD endpoints for physical collateral items held in agency vault & return receipt generation.
"""

from fastapi import APIRouter, Request, Query, Path
from starlette.responses import Response
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any

from dashboard.collateral_payment_method import InvalidCollateralPaymentMethod
from dashboard.services.collateral_service import (
    add_collateral_item,
    list_collateral_items,
    return_collateral_item,
    set_collateral_payment_method,
    generate_collateral_receipt_pdf
)

collateral_bp = APIRouter(prefix="/api/collateral", tags=["collateral"])


def _session_audit_actor(request: Request) -> Optional[str]:
    """Session email, else the session agent name. Never the request body.

    Same identity #165 uses for attest-id. A missing session, or a session
    with neither email nor agent name, returns None so the route can 401.
    """
    from dashboard.auth.agent_scope import agent_identity
    from dashboard.auth.pin_middleware import get_session_from_request

    sess = get_session_from_request(request)
    if not sess or not sess.get("auth"):
        return None
    ident = agent_identity(request)
    actor = str(ident.get("email") or ident.get("agent_name") or "").strip()
    return actor or None


def _auth_required() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"success": False, "error": "auth_required"},
    )

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
    actor = _session_audit_actor(request)
    if not actor:
        return _auth_required()
    try:
        data = await request.json() or {}
        item = await add_collateral_item(data, actor=actor)
        return JSONResponse(status_code=200, content={"success": True, "item": item})
    except InvalidCollateralPaymentMethod as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "invalid_collateral_payment_method", "message": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@collateral_bp.post("/payment-method/{collateral_id}")
async def update_collateral_payment_method(
    collateral_id: str = Path(...),
    request: Request = None,
):
    """Set or change how this collateral was paid. Premium method is not accepted here."""
    actor = _session_audit_actor(request) if request is not None else None
    if not actor:
        return _auth_required()
    try:
        data = await request.json() or {}
        item = await set_collateral_payment_method(collateral_id, data, actor=actor)
        return JSONResponse(status_code=200, content={"success": True, "item": item})
    except InvalidCollateralPaymentMethod as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "invalid_collateral_payment_method", "message": str(exc)},
        )
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
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
