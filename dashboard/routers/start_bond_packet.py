"""Staff start-bond-packet screen. Hidden unless SAAS_MULTI_TENANT is on.

Does not register a public path and does not revive POST /api/write-bond.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.auth.agent_scope import poa_scope_query
from dashboard.services.charge_verbatim import ChargeCapacityError, capacity_error_body
from dashboard.services.start_bond_packet import (
    PacketStartConflict,
    PacketStartDisabled,
    PacketStartError,
    prepare_packet,
    send_packet,
)
from dashboard.tenancy.flag import multi_tenant_enabled

router = APIRouter(tags=["start-bond-packet"])
_PAGE = Path(__file__).resolve().parents[1] / "start_bond_packet.html"


def _disabled():
    return JSONResponse({"error": "not_found"}, status_code=404)


def _poa_filter(request: Request) -> dict | None:
    scope = poa_scope_query(request)
    return scope or None


@router.get("/bond-packet")
async def packet_page():
    if not multi_tenant_enabled():
        return _disabled()
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.post("/api/bond-packet/prepare")
async def api_prepare(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    payload = await request.json()
    payload = payload if isinstance(payload, dict) else {}
    try:
        return await prepare_packet(
            str(payload.get("booking_number") or ""),
            str(payload.get("surety_id") or ""),
            poa_filter=_poa_filter(request),
        )
    except PacketStartDisabled:
        return _disabled()
    except PacketStartError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)


@router.post("/api/bond-packet/send")
async def api_send(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    payload = await request.json()
    try:
        result = await send_packet(payload if isinstance(payload, dict) else {}, poa_filter=_poa_filter(request))
    except PacketStartDisabled:
        return _disabled()
    except PacketStartConflict as exc:
        return JSONResponse(
            {"error": exc.code, "message": exc.message, "packet_id": exc.packet_id},
            status_code=409,
        )
    except ChargeCapacityError as exc:
        return JSONResponse(capacity_error_body(exc), status_code=422)
    except PacketStartError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    status = 200 if result.get("state") == "ready_for_staff_send" else 409
    return JSONResponse(result, status_code=status)
