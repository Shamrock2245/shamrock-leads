"""
Remittitur Clock API Blueprint — ShamrockLeads
Endpoints for F.S. 903.26 Forfeiture Remittitur & Summary Judgment Watchdog.
"""

from fastapi import APIRouter, Request, Query, Path
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any

from dashboard.services.remittitur_clock_service import (
    get_forfeiture_remittitur_clocks,
    update_remittitur_status
)

remittitur_bp = APIRouter(prefix="/api/forfeitures", tags=["forfeitures"])

@remittitur_bp.get("/remittitur-clock")
async def get_remittitur_watchdog_clock():
    """Get active forfeiture remittitur countdown clocks under F.S. 903.26."""
    try:
        clocks = await get_forfeiture_remittitur_clocks()
        return JSONResponse(status_code=200, content={"success": True, "count": len(clocks), "forfeitures": clocks})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@remittitur_bp.post("/update-status")
async def update_forfeiture_motion_status(request: Request):
    """Update motion-to-vacate status and assigned attorney for a forfeited bond."""
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number")
        motion_status = data.get("motion_status", "pending")
        attorney = data.get("assigned_attorney")

        if not booking_number:
            return JSONResponse(status_code=400, content={"success": False, "error": "Missing booking_number"})

        res = await update_remittitur_status(booking_number, motion_status, attorney=attorney)
        return JSONResponse(status_code=200, content=res)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
