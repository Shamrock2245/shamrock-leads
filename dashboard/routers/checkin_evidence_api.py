"""
Staff-only missed check-in evidence pack and enrollment SLA list.

GET /api/checkin/evidence/{booking_number}
GET /api/checkin/evidence/{booking_number}/download?format=zip|pdf
GET /api/checkin/enrollment-sla

Recovery is not on the recovery allowlist for these paths. Handlers also
refuse recovery and sub-agent sessions.
"""
from __future__ import annotations

import logging
import os
import secrets

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from dashboard.auth.pin_middleware import (
    DASHBOARD_PIN,
    get_session_from_request,
    is_machine_auth_valid,
)
from dashboard.auth.recovery_scope import session_is_recovery, session_is_staff
from dashboard.services.checkin_evidence_service import (
    audit_evidence_download,
    clamp_limit,
    list_signed_bonds_checkin_not_sent,
    load_evidence_manifest,
    render_evidence_pdf,
    render_evidence_zip,
    safe_booking_filename,
)

logger = logging.getLogger("shamrock.checkin_evidence")

checkin_evidence_bp = APIRouter(prefix="/api/checkin", tags=["checkin-evidence"])


def _actor(request: Request) -> str:
    sess = get_session_from_request(request) or {}
    return str(sess.get("email") or sess.get("agent_name") or "staff").strip() or "staff"


def _staff_auth(request: Request) -> tuple[bool, str, int]:
    """Staff, admin, or god_admin. Recovery and sub-agent are 403."""
    sess = get_session_from_request(request)
    if sess and sess.get("auth"):
        if session_is_recovery(request) or str(sess.get("role") or "") == "sub_agent":
            return False, "", 403
        if session_is_staff(request):
            return True, _actor(request), 200
        return False, "", 403
    if is_machine_auth_valid(request):
        actor = request.headers.get("X-Staff-Email", "").strip() or "machine_key"
        return True, actor, 200
    pin = (os.getenv("DASHBOARD_PIN") or DASHBOARD_PIN or "").strip()
    token = (
        request.headers.get("X-Admin-Token")
        or request.headers.get("X-PIN")
        or request.headers.get("X-Staff-PIN")
        or ""
    ).strip()
    if pin and token and secrets.compare_digest(token, pin):
        actor = request.headers.get("X-Staff-Email", "").strip() or "staff_pin"
        return True, actor, 200
    return False, "", 401


def _denied(status: int) -> JSONResponse:
    if status == 403:
        return JSONResponse(
            {
                "success": False,
                "error": "forbidden",
                "message": "Check-in evidence is staff-only",
            },
            status_code=403,
        )
    return JSONResponse(
        {"success": False, "error": "unauthorized", "message": "Staff session required"},
        status_code=401,
    )


def _manifest_public(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if not str(k).startswith("_")}


@checkin_evidence_bp.get("/enrollment-sla")
async def enrollment_sla(request: Request, limit: int = Query(default=100)):
    ok, _actor_name, status = _staff_auth(request)
    if not ok:
        return _denied(status)
    data = await list_signed_bonds_checkin_not_sent(limit=limit)
    return data


@checkin_evidence_bp.get("/evidence/{booking_number}")
async def evidence_manifest(
    request: Request,
    booking_number: str,
    limit: int = Query(default=20),
):
    ok, _actor_name, status = _staff_auth(request)
    if not ok:
        return _denied(status)
    manifest = await load_evidence_manifest(booking_number, limit=clamp_limit(limit))
    if manifest is None:
        return JSONResponse(
            {"success": False, "error": "booking_not_found", "booking_number": booking_number},
            status_code=404,
        )
    body = _manifest_public(manifest)
    body["success"] = True
    return body


@checkin_evidence_bp.get("/evidence/{booking_number}/download")
async def evidence_download(
    request: Request,
    booking_number: str,
    format: str = Query(default="zip"),
    limit: int = Query(default=20),
):
    ok, actor, status = _staff_auth(request)
    if not ok:
        return _denied(status)
    fmt = (format or "zip").strip().lower()
    if fmt not in ("zip", "pdf"):
        return JSONResponse(
            {"success": False, "error": "format_must_be_zip_or_pdf"},
            status_code=400,
        )
    manifest = await load_evidence_manifest(booking_number, limit=clamp_limit(limit))
    if manifest is None:
        return JSONResponse(
            {"success": False, "error": "booking_not_found", "booking_number": booking_number},
            status_code=404,
        )
    stem = safe_booking_filename(booking_number)
    await audit_evidence_download(
        booking_number=booking_number,
        actor=actor,
        entry_count=len(manifest.get("entries") or []),
        empty=bool(manifest.get("empty")),
    )
    if fmt == "pdf":
        payload = render_evidence_pdf(manifest)
        media = "application/pdf"
        filename = f"Checkin_Evidence_{stem}.pdf"
    else:
        payload = render_evidence_zip(manifest)
        media = "application/zip"
        filename = f"Checkin_Evidence_{stem}.zip"
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
