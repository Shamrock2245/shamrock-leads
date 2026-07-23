"""
OSINT Intelligence API Router v2 — ShamrockLeads
=================================================
Admin-only endpoints for multi-engine OSINT research on defendants and indemnitors.

Engines: Maigret · Sherlock · Blackbird · SpiderFoot

All endpoints require admin authorization via:
  1. Signed session cookie with admin role, OR
  2. X-Admin-Key matching OSINT_ADMIN_KEY, OR
  3. X-Admin-Token matching DASHBOARD_PIN

Endpoints:
  POST   /api/osint/scan                       → Initiate multi-engine scan
  GET    /api/osint/scan/{id}                   → Get scan status + results
  GET    /api/osint/scan/{id}/raw               → Full report with raw tool output
  GET    /api/osint/scans                       → List scans (filter/sort/paginate)
  PATCH  /api/osint/scan/{id}/findings          → Mark findings relevant/irrelevant
  POST   /api/osint/scan/{id}/attach            → Copy summary to subject record
  GET    /api/osint/scan/{id}/export/json       → Export full JSON
  GET    /api/osint/scan/{id}/export/csv        → Export flat CSV
  GET    /api/osint/scan/{id}/export/pdf        → Export PDF summary
  GET    /api/osint/status                      → Tool availability + queue info
  POST   /api/osint/trape/session               → Create Trape tracking session
  POST   /api/osint/trape/session/{id}/update   → Update session with collected data
  GET    /api/osint/trape/sessions              → List Trape sessions
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from dashboard.auth.pin_middleware import session_is_admin
from dashboard.models.osint import (
    FindingRelevanceUpdate,
    OSINTScanRequest,
    TrapeSessionRequest,
)
from dashboard.services.osint_service import get_osint_service

log = logging.getLogger("shamrock.osint_api")

router = APIRouter(prefix="/api/osint", tags=["osint"])

OSINT_ADMIN_KEY = os.getenv("OSINT_ADMIN_KEY") or os.getenv("DASHBOARD_PIN", "")
DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")


# ── Admin Guard ───────────────────────────────────────────────────────────────

def _require_admin(
    request: Request,
    x_admin_key: Optional[str] = None,
    x_admin_token: Optional[str] = None,
) -> None:
    """Enforce admin-only access."""
    if session_is_admin(request):
        return
    if OSINT_ADMIN_KEY and x_admin_key and x_admin_key == OSINT_ADMIN_KEY:
        return
    if DASHBOARD_PIN and x_admin_token and x_admin_token == DASHBOARD_PIN:
        return
    if not OSINT_ADMIN_KEY and not DASHBOARD_PIN:
        return  # Dev mode
    raise HTTPException(
        status_code=403,
        detail="OSINT module requires admin authorization.",
    )


# ── Tool Status ───────────────────────────────────────────────────────────────

@router.get("/status", summary="Check OSINT tool availability and queue info")
async def osint_status(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Returns availability of all 4 engines + queue depth."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    tools = svc.probe_tools()
    queue_info = await svc.get_queue_info()
    tools["queue"] = queue_info
    tools["admin_key_configured"] = bool(OSINT_ADMIN_KEY)
    return tools


# ── Scan Endpoints ────────────────────────────────────────────────────────────

@router.post("/scan", summary="Initiate multi-engine OSINT scan")
async def initiate_scan(
    body: OSINTScanRequest,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Initiates an asynchronous multi-engine OSINT scan.
    Returns immediately with a scan_id. Poll GET /api/osint/scan/{id} for results.
    """
    _require_admin(request, x_admin_key, x_admin_token)

    # Validate at least one search identifier
    has_identifier = any([body.full_name, body.usernames, body.email, body.phone])
    if not has_identifier:
        raise HTTPException(
            status_code=422,
            detail="At least one identifier required: full_name, usernames, email, or phone.",
        )

    svc = get_osint_service()
    probe = svc.probe_tools()
    if not probe.get("ready_for_scans"):
        raise HTTPException(
            status_code=503,
            detail="No OSINT engines available on worker (ready_for_scans=false).",
        )

    try:
        scan_id = await svc.run_scan(body, actor="admin")
    except Exception as exc:
        log.error("Failed to initiate OSINT scan: %s", exc)
        raise HTTPException(status_code=500, detail=f"Scan initiation failed: {exc}") from exc

    return {
        "scan_id": scan_id,
        "status": "running",
        "engines": [e.value for e in body.engines],
        "message": f"Scan initiated. Poll GET /api/osint/scan/{scan_id} for results.",
    }


@router.get("/scan/{scan_id}", summary="Get OSINT scan status and results")
async def get_scan(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Retrieve scan status and results (raw outputs excluded)."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    scan = await svc.get_scan(scan_id, include_raw=False)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return scan


@router.get("/scan/{scan_id}/raw", summary="Get full scan with raw tool output")
async def get_scan_raw(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Full scan including raw engine outputs. Forensic use only."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    scan = await svc.get_scan(scan_id, include_raw=True)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return scan


@router.get("/scans", summary="List OSINT scans")
async def list_scans(
    request: Request,
    subject_id: Optional[str] = Query(None),
    subject_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    engine: Optional[str] = Query(None, description="Filter by engine used"),
    search: Optional[str] = Query(None, description="Search by name"),
    sort: str = Query("newest", description="newest, oldest, accounts"),
    limit: int = Query(25, ge=1, le=100),
    skip: int = Query(0, ge=0),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """List scans with filtering, sorting, and pagination."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    scans, total = await svc.list_scans(
        subject_id=subject_id,
        subject_type=subject_type,
        status=status,
        engine=engine,
        search=search,
        sort=sort,
        limit=limit,
        skip=skip,
    )
    return {"scans": scans, "total": total, "limit": limit, "skip": skip}


# ── Finding Management ────────────────────────────────────────────────────────

@router.patch("/scan/{scan_id}/findings", summary="Mark findings relevant/irrelevant")
async def update_findings(
    scan_id: str,
    body: FindingRelevanceUpdate,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Update relevance status of specific accounts or entities."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    updated = await svc.update_findings_relevance(scan_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return {"success": True, "scan_id": scan_id}


@router.post("/scan/{scan_id}/attach", summary="Attach OSINT summary to subject record")
async def attach_to_subject(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Write a clean OSINT summary into the defendant's/indemnitor's record."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    result = await svc.attach_to_subject(scan_id, actor="admin")
    if not result:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return result


# ── Export Endpoints ──────────────────────────────────────────────────────────

@router.get("/scan/{scan_id}/export/json", summary="Export scan as JSON")
async def export_json(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Full structured JSON dump for case management."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    data = await svc.export_json(scan_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return data


@router.get("/scan/{scan_id}/export/csv", summary="Export scan as CSV")
async def export_csv(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Flat CSV of accounts and contacts for case file."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    csv_content = await svc.export_csv(scan_id)
    if csv_content is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=osint_scan_{scan_id}.csv"},
    )


@router.get("/scan/{scan_id}/export/pdf", summary="Export scan as PDF")
async def export_pdf(
    scan_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Human-readable PDF summary for the case file."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    pdf_bytes = await svc.export_pdf(scan_id)
    if pdf_bytes is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found.")
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=osint_report_{scan_id}.pdf"},
    )


# ── Trape Endpoints (unchanged) ──────────────────────────────────────────────

@router.post("/trape/session", summary="Create a Trape tracking session")
async def create_trape_session(
    body: TrapeSessionRequest,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Generate a Trape tracking session for skip-trace operations."""
    _require_admin(request, x_admin_key, x_admin_token)
    svc = get_osint_service()
    try:
        session = await svc.create_trape_session(
            subject_type=body.subject_type.value,
            subject_id=body.subject_id,
            lure_url=body.lure_url,
            notes=body.notes,
            actor="admin",
        )
    except Exception as exc:
        log.error("Failed to create Trape session: %s", exc)
        raise HTTPException(status_code=500, detail=f"Session creation failed: {exc}") from exc
    return session


@router.post("/trape/session/{session_id}/update", summary="Update Trape session")
async def update_trape_session(
    session_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Update a Trape session with data collected from the target's browser."""
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
