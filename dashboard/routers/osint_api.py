"""
OSINT Intelligence API Router — ShamrockLeads
=============================================
Admin-only endpoints for OSINT background research on defendants and indemnitors.

All endpoints require:
  1. Standard PIN authentication (via PinAuthMiddleware cookie).
  2. X-Admin-Key header matching the OSINT_ADMIN_KEY environment variable.

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
import shutil
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from dashboard.models.osint import OSINTScanRequest, TrapeSessionRequest
from dashboard.services.osint_service import get_osint_service

log = logging.getLogger("shamrock.osint_api")

router = APIRouter(prefix="/api/osint", tags=["osint"])

# OSINT_ADMIN_KEY takes precedence; fall back to DASHBOARD_PIN for consistency
# with the existing bonds.py admin guard pattern.
OSINT_ADMIN_KEY = os.getenv("OSINT_ADMIN_KEY") or os.getenv("DASHBOARD_PIN", "")

# PII fields that must never appear in audit event details
_PII_FIELDS = {"phone", "ssn", "address", "dob", "email", "name", "full_name",
               "first_name", "last_name", "date_of_birth", "social_security"}


def _scrub_pii(details: dict) -> dict:
    """Return a copy of audit details with PII fields removed."""
    return {k: v for k, v in details.items() if k.lower() not in _PII_FIELDS}


# ── Admin Key Guard ────────────────────────────────────────────────────────────

def _require_admin(x_admin_key: Optional[str]) -> None:
    """
    Enforce admin-only access on top of the existing PIN middleware.
    Consistent with the X-Admin-Token pattern used in bonds.py.
    If neither OSINT_ADMIN_KEY nor DASHBOARD_PIN is set, PIN auth is sufficient.
    """
    if not OSINT_ADMIN_KEY:
        return  # No key configured — PIN auth is sufficient
    if x_admin_key != OSINT_ADMIN_KEY:
        raise HTTPException(
            status_code=403,
            detail="OSINT module requires admin authorization. Provide X-Admin-Key header.",
        )


# ── Tool Status ────────────────────────────────────────────────────────────────

@router.get("/status", summary="Check OSINT tool availability")
async def osint_status(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Returns the availability status of Maigret, Blackbird, and Trape."""
    _require_admin(x_admin_key)

    maigret_path = shutil.which("maigret") or os.getenv("MAIGRET_PATH", "")
    blackbird_dir = os.getenv("BLACKBIRD_DIR", "/opt/blackbird")
    blackbird_path = os.path.join(blackbird_dir, "blackbird.py")
    trape_dir = os.getenv("TRAPE_DIR", "/opt/trape")
    trape_path = os.path.join(trape_dir, "trape.py")
    trape_server = os.getenv("TRAPE_SERVER_URL", "")

    return {
        "maigret": {
            "available": bool(maigret_path and os.path.exists(maigret_path)),
            "path": maigret_path or "not found — install with: pip install maigret",
        },
        "blackbird": {
            "available": os.path.exists(blackbird_path),
            "path": blackbird_path if os.path.exists(blackbird_path) else (
                "not found — clone from: https://github.com/p1ngul1n0/blackbird"
            ),
        },
        "trape": {
            "available": os.path.exists(trape_path),
            "path": trape_path if os.path.exists(trape_path) else (
                "not found — clone from: https://github.com/jofpin/trape"
            ),
            "server_url": trape_server or "not configured — set TRAPE_SERVER_URL env var",
            "note": (
                "Trape requires manual server startup and a publicly accessible URL. "
                "Use /api/osint/trape/session to generate session payloads."
            ),
        },
        "admin_key_configured": bool(OSINT_ADMIN_KEY),
    }


# ── Scan Endpoints ─────────────────────────────────────────────────────────────

@router.post("/scan", summary="Initiate OSINT scan (Maigret + Blackbird)")
async def initiate_scan(
    body: OSINTScanRequest,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Initiates an asynchronous OSINT scan for a defendant or indemnitor.
    Returns immediately with a report_id. Poll GET /api/osint/report/{id} for results.
    """
    _require_admin(x_admin_key)

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
            notes=body.notes,
            actor="admin",
        )
    except Exception as exc:
        log.error("Failed to initiate OSINT scan: %s", exc)
        raise HTTPException(status_code=500, detail=f"Scan initiation failed: {exc}") from exc

    return {
        "report_id": report_id,
        "status": "running",
        "message": "Scan initiated. Poll GET /api/osint/report/{report_id} for results.",
    }


@router.get("/report/{report_id}", summary="Get OSINT scan report")
async def get_report(
    report_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Retrieve a completed OSINT report by ID (raw tool JSON excluded)."""
    _require_admin(x_admin_key)

    svc = get_osint_service()
    report = await svc.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    return report


@router.get("/report/{report_id}/raw", summary="Get full OSINT report with raw tool output")
async def get_raw_report(
    report_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Retrieve the full OSINT report including raw Maigret and Blackbird JSON.
    For forensic/investigative use only.
    """
    _require_admin(x_admin_key)

    svc = get_osint_service()
    report = await svc.get_raw_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    return report


@router.get("/reports", summary="List OSINT reports")
async def list_reports(
    subject_id: Optional[str] = Query(None, description="Filter by subject MongoDB ID"),
    subject_type: Optional[str] = Query(None, description="Filter by 'defendant' or 'indemnitor'"),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """List OSINT reports, optionally filtered by subject."""
    _require_admin(x_admin_key)

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
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
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
    _require_admin(x_admin_key)

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
):
    """
    Update a Trape session with data collected from the target's browser.
    This endpoint can be called by a Trape webhook or manually by the operator.
    """
    _require_admin(x_admin_key)

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
    subject_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """List all Trape tracking sessions."""
    _require_admin(x_admin_key)

    svc = get_osint_service()
    sessions = await svc.list_trape_sessions(subject_id=subject_id, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}
