"""
OSINT Intelligence API Router — ShamrockLeads
=============================================
Admin-only endpoints for OSINT background research on defendants and indemnitors.

All endpoints require:
  1. Standard PIN authentication (via PinAuthMiddleware cookie), AND
  2. Either:
       - an admin session (role=admin / ADMIN_EMAILS), OR
       - X-Admin-Key matching OSINT_ADMIN_KEY (or DASHBOARD_PIN fallback), OR
       - X-Admin-Token matching DASHBOARD_PIN

Endpoints:
  POST   /api/osint/scan                    → Initiate Maigret + Blackbird scan
  GET    /api/osint/report/{id}             → Get scan report (no raw JSON)
  GET    /api/osint/report/{id}/raw         → Get full report with raw tool output
  GET    /api/osint/reports                 → List reports (filterable by subject)
  POST   /api/osint/trape/session           → Create a Trape tracking session
  POST   /api/osint/trape/session/{id}/update → Update session with collected data
  GET    /api/osint/trape/sessions          → List Trape sessions
  GET    /api/osint/status                  → Tool availability check
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from dashboard.auth.pin_middleware import session_is_admin
from dashboard.models.osint import OSINTScanRequest, TrapeSessionRequest
from dashboard.services.osint_service import get_osint_service

log = logging.getLogger("shamrock.osint_api")

router = APIRouter(prefix="/api/osint", tags=["osint"])

# OSINT_ADMIN_KEY takes precedence; fall back to DASHBOARD_PIN for consistency
# with the existing bonds.py admin guard pattern.
OSINT_ADMIN_KEY = os.getenv("OSINT_ADMIN_KEY") or os.getenv("DASHBOARD_PIN", "")
DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")

# PII fields that must never appear in audit event details
_PII_FIELDS = {
    "phone", "ssn", "address", "dob", "email", "name", "full_name",
    "first_name", "last_name", "date_of_birth", "social_security",
}


def _scrub_pii(details: dict) -> dict:
    """Return a copy of audit details with PII fields removed."""
    return {k: v for k, v in details.items() if k.lower() not in _PII_FIELDS}


# ── Admin Guard ────────────────────────────────────────────────────────────────

def _require_admin(
    request: Request,
    x_admin_key: Optional[str] = None,
    x_admin_token: Optional[str] = None,
) -> None:
    """
    Enforce admin-only access on top of the existing PIN middleware.

    Accepts (any one is enough):
      1. Signed session cookie with admin role / admin email
      2. X-Admin-Key == OSINT_ADMIN_KEY (or DASHBOARD_PIN fallback)
      3. X-Admin-Token == DASHBOARD_PIN (bonds-style header)
    """
    if session_is_admin(request):
        return

    if OSINT_ADMIN_KEY and x_admin_key and x_admin_key == OSINT_ADMIN_KEY:
        return

    if DASHBOARD_PIN and x_admin_token and x_admin_token == DASHBOARD_PIN:
        return

    # Dev mode: no PIN / key configured
    if not OSINT_ADMIN_KEY and not DASHBOARD_PIN:
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "OSINT module requires admin authorization. "
            "Log in as admin, or provide X-Admin-Key / X-Admin-Token."
        ),
    )


# ── Tool Status ────────────────────────────────────────────────────────────────

@router.get("/status", summary="Check OSINT tool availability")
async def osint_status(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Returns the availability status of Maigret, Blackbird, and Trape."""
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    tools = svc.probe_tools()
    tools["admin_key_configured"] = bool(OSINT_ADMIN_KEY)
    tools["ready_for_scans"] = bool(tools.get("ready_for_scans"))
    return tools


# ── Scan Endpoints ─────────────────────────────────────────────────────────────

@router.post("/scan", summary="Initiate OSINT scan (Maigret + Blackbird)")
async def initiate_scan(
    body: OSINTScanRequest,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Initiates an asynchronous OSINT scan for a defendant or indemnitor.
    Returns immediately with a report_id. Poll GET /api/osint/report/{id} for results.
    """
    _require_admin(request, x_admin_key, x_admin_token)

    # Validate at least one search identifier is provided
    has_identifier = any([
        body.full_name,
        body.usernames,
        body.email,
    ])
    if not has_identifier:
        raise HTTPException(
            status_code=422,
            detail="At least one search identifier is required: full_name, usernames, or email.",
        )

    svc = get_osint_service()
    probe = svc.probe_tools()
    if not probe.get("worker_reachable", True) and not probe.get("ready_for_scans"):
        raise HTTPException(
            status_code=503,
            detail=(
                "osint-worker is unreachable. "
                f"{probe.get('error') or 'Start the osint-worker service.'}"
            ),
        )
    if not probe.get("ready_for_scans"):
        raise HTTPException(
            status_code=503,
            detail="No OSINT tools available on worker (ready_for_scans=false).",
        )

    try:
        report_id = await svc.run_scan(
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            full_name=body.full_name,
            usernames=body.usernames,
            email=body.email,
            deep_scan=body.deep_scan,
            run_maigret=body.run_maigret,
            run_blackbird=body.run_blackbird,
            second_opinion=body.second_opinion,
            notes=body.notes,
            actor="admin",
        )
    except Exception as exc:
        log.error("Failed to initiate OSINT scan: %s", exc)
        raise HTTPException(status_code=500, detail=f"Scan initiation failed: {exc}") from exc

    return {
        "report_id": report_id,
        "status": "running",
        "message": "Scan initiated via osint-worker. Poll GET /api/osint/report/{report_id}.",
        "worker": probe.get("worker_url"),
        "tools": {
            "maigret": probe.get("maigret", {}).get("available"),
            "blackbird": probe.get("blackbird", {}).get("available"),
        },
        "policy_defaults": probe.get("defaults") or {
            "maigret_default": True,
            "blackbird_default": False,
            "blackbird_on_email": True,
            "blackbird_on_second_opinion": True,
        },
    }


@router.get("/report/{report_id}", summary="Get OSINT scan report")
async def get_report(
    report_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Retrieve a completed OSINT report by ID (raw tool JSON excluded)."""
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    report = await svc.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    return report


@router.get("/report/{report_id}/raw", summary="Get full OSINT report with raw tool output")
async def get_raw_report(
    report_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Retrieve the full OSINT report including raw Maigret and Blackbird JSON.
    For forensic/investigative use only.
    """
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    report = await svc.get_raw_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    return report


@router.get("/reports", summary="List OSINT reports")
async def list_reports(
    request: Request,
    subject_id: Optional[str] = Query(None, description="Filter by subject MongoDB ID"),
    subject_type: Optional[str] = Query(None, description="Filter by 'defendant' or 'indemnitor'"),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """List OSINT reports, optionally filtered by subject."""
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    reports = await svc.list_reports(
        subject_id=subject_id,
        subject_type=subject_type,
        limit=limit,
    )
    return {"reports": reports, "count": len(reports)}


# ── Trape Endpoints ────────────────────────────────────────────────────────────

@router.post("/trape/session", summary="Create a Trape tracking session")
async def create_trape_session(
    body: TrapeSessionRequest,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Generates a Trape tracking session for skip-trace operations.

    Returns the session document including the `trape_command` the operator
    must run manually on the server to activate the tracking server.
    The `tracking_url` is the URL to send to the subject (via social engineering).

    **Operational Requirements:**
    - A publicly accessible server or ngrok tunnel is required.
    - Set TRAPE_SERVER_URL and TRAPE_DIR environment variables.
    - The subject must visit the tracking URL for data collection to occur.
    """
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    try:
        session = await svc.create_trape_session(
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            lure_url=body.lure_url,
            notes=body.notes,
            actor="admin",
        )
    except Exception as exc:
        log.error("Failed to create Trape session: %s", exc)
        raise HTTPException(status_code=500, detail=f"Session creation failed: {exc}") from exc

    return session


@router.post("/trape/session/{session_id}/update", summary="Update Trape session with collected data")
async def update_trape_session(
    session_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Update a Trape session with data collected from the target's browser.
    This endpoint can be called by a Trape webhook or manual entry.
    """
    _require_admin(request, x_admin_key, x_admin_token)

    try:
        data = await request.json()
    except Exception:
        data = {}

    svc = get_osint_service()
    updated = await svc.update_trape_session(session_id=session_id, data=data, actor="admin")
    if not updated:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")
    return {"success": True, "session_id": session_id}


@router.get("/trape/sessions", summary="List Trape tracking sessions")
async def list_trape_sessions(
    request: Request,
    subject_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """List all Trape tracking sessions."""
    _require_admin(request, x_admin_key, x_admin_token)

    svc = get_osint_service()
    sessions = await svc.list_trape_sessions(subject_id=subject_id, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}
