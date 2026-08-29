"""Staff-session endpoint: merge a jail-roster bookmarklet extract onto an ArrestLead."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.services.booking_extract_merge import (
    BookingExtractError,
    merge_booking_extract,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["booking-extract"])


def _actor(request: Request, body: dict) -> str:
    raw = (body.get("changed_by") or body.get("actor") or "").strip()
    if raw:
        return raw[:80]
    try:
        from dashboard.auth.pin_middleware import get_session_from_request
        sess = get_session_from_request(request) or {}
        return str(sess.get("agent_name") or sess.get("email") or "dashboard_user")[:80]
    except Exception:
        return "dashboard_user"


@router.post("/leads/merge-booking-extract")
async def api_merge_booking_extract(request: Request):
    """Merge Lee (or other) booking-page JSON onto County + Booking_Number.

    Session-gated (PIN middleware). Does not create paperwork, POA, or contact anyone.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "JSON object required", "code": "invalid"}, status_code=400)

    payload = body.get("extract") if isinstance(body.get("extract"), dict) else body
    try:
        result = await merge_booking_extract(payload, actor=_actor(request, body))
        return result
    except BookingExtractError as exc:
        return JSONResponse(
            {"success": False, "error": exc.message, "code": exc.code},
            status_code=exc.status,
        )
    except Exception:
        logger.exception("[booking-extract] merge failed")
        return JSONResponse(
            {"success": False, "error": "Merge failed", "code": "internal"},
            status_code=500,
        )
