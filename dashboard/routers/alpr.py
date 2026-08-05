"""
ALPR / LPR API — ShamrockLeads
==============================
FL511 public camera plate watch + ad-hoc image scan.

Endpoints:
  GET  /api/alpr/status      — worker health & active camera counts
  GET  /api/alpr/hits        — historical plate hits
  POST /api/alpr/watchlist   — add plate + defendant to watchlist
  GET  /api/alpr/watchlist   — list active watchlist entries
  POST /api/alpr/scan-image  — upload vehicle photo → plate detections
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from dashboard.auth.pin_middleware import get_session_from_request, session_is_admin
from dashboard.deps import get_collection

log = logging.getLogger("shamrock.alpr_api")

router = APIRouter(prefix="/api/alpr", tags=["alpr"])

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")
ALPR_ADMIN_KEY = os.getenv("ALPR_ADMIN_KEY") or os.getenv("OSINT_ADMIN_KEY") or DASHBOARD_PIN


def _require_staff(
    request: Request,
    x_admin_key: Optional[str] = None,
    x_admin_token: Optional[str] = None,
) -> None:
    """PIN middleware already gates /api/*; allow any valid staff session + admin keys."""
    if get_session_from_request(request) or session_is_admin(request):
        return
    if ALPR_ADMIN_KEY and x_admin_key and x_admin_key == ALPR_ADMIN_KEY:
        return
    if DASHBOARD_PIN and x_admin_token and x_admin_token == DASHBOARD_PIN:
        return
    if not ALPR_ADMIN_KEY and not DASHBOARD_PIN:
        return  # Dev open
    raise HTTPException(status_code=403, detail="ALPR requires authenticated staff session.")


def _normalize_plate(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


class WatchlistCreate(BaseModel):
    plate_text: str = Field(..., description="License plate number")
    defendant_id: str = Field(..., description="Defendant ID / UUID")
    defendant_name: str = ""
    case_number: str = ""
    notes: str = ""


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def alpr_status(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Probe ALPR worker health & active camera stream counts."""
    _require_staff(request, x_admin_key, x_admin_token)

    from services.alpr_engine import probe_alpr_deps
    from services.alpr_cameras import enabled_cameras, load_camera_registry

    deps = probe_alpr_deps()
    registry = load_camera_registry()
    enabled = enabled_cameras(registry)

    worker_doc: Dict[str, Any] = {}
    try:
        col = get_collection("alpr_worker_status")
        worker_doc = await col.find_one({"_id": "alpr-worker"}) or {}
        # Serialize datetime
        if worker_doc.get("updated_at") and hasattr(worker_doc["updated_at"], "isoformat"):
            worker_doc["updated_at"] = worker_doc["updated_at"].isoformat()
    except Exception as exc:
        worker_doc = {"error": str(exc)[:200]}

    hits_24h = 0
    watch_count = 0
    try:
        from datetime import timedelta

        hits_col = get_collection("lpr_hits")
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        hits_24h = await hits_col.count_documents({"timestamp": {"$gte": since}})
        watch_count = await get_collection("lpr_watchlist").count_documents(
            {"active": {"$ne": False}}
        )
    except Exception:
        pass

    streams = (worker_doc.get("streams") or {}) if isinstance(worker_doc, dict) else {}
    return {
        "ok": True,
        "service": "alpr",
        "deps": deps,
        "cameras_registered": len(registry),
        "cameras_enabled": len(enabled),
        "cameras_connected": streams.get("cameras_connected"),
        "worker": {
            "engine_ready": worker_doc.get("engine_ready"),
            "engine_error": worker_doc.get("engine_error"),
            "cycle": worker_doc.get("cycle"),
            "hits_total": worker_doc.get("hits_total"),
            "updated_at": worker_doc.get("updated_at"),
            "last_error": worker_doc.get("last_error"),
            "streams": streams,
        },
        "hits_last_24h": hits_24h,
        "watchlist_count": watch_count,
        "env": {
            "alpr_enabled": os.getenv("ALPR_ENABLED", "true"),
            "frame_interval_s": os.getenv("ALPR_FRAME_INTERVAL_S", "2.5"),
        },
    }


# ── Hits ──────────────────────────────────────────────────────────────────────

@router.get("/hits")
async def alpr_hits(
    request: Request,
    plate: Optional[str] = Query(None, description="Filter by plate text"),
    defendant_id: Optional[str] = Query(None),
    matched_only: bool = Query(True, description="Only watchlist matches"),
    since: Optional[str] = Query(None, description="ISO date/time lower bound"),
    until: Optional[str] = Query(None, description="ISO date/time upper bound"),
    limit: int = Query(50, ge=1, le=500),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Query historical plate hits."""
    _require_staff(request, x_admin_key, x_admin_token)

    q: Dict[str, Any] = {}
    if plate:
        q["plate_text"] = _normalize_plate(plate)
    if defendant_id:
        q["matched_defendant_id"] = str(defendant_id)
    if matched_only:
        q["matched"] = True

    ts_q: Dict[str, Any] = {}
    if since:
        try:
            ts_q["$gte"] = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, detail="Invalid since (use ISO-8601)")
    if until:
        try:
            ts_q["$lte"] = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, detail="Invalid until (use ISO-8601)")
    if ts_q:
        q["timestamp"] = ts_q

    col = get_collection("lpr_hits")
    cursor = col.find(q).sort("timestamp", -1).limit(limit)
    hits: List[Dict[str, Any]] = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        for k in ("timestamp", "created_at"):
            if doc.get(k) and hasattr(doc[k], "isoformat"):
                doc[k] = doc[k].isoformat()
        hits.append(doc)

    return {"ok": True, "count": len(hits), "hits": hits}


# ── Watchlist ─────────────────────────────────────────────────────────────────

@router.get("/watchlist")
async def list_watchlist(
    request: Request,
    active_only: bool = Query(True),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    _require_staff(request, x_admin_key, x_admin_token)
    q: Dict[str, Any] = {}
    if active_only:
        q["active"] = {"$ne": False}
    col = get_collection("lpr_watchlist")
    items = []
    async for doc in col.find(q).sort("updated_at", -1).limit(500):
        doc["_id"] = str(doc["_id"])
        for k in ("created_at", "updated_at", "first_seen_at"):
            if doc.get(k) and hasattr(doc[k], "isoformat"):
                doc[k] = doc[k].isoformat()
        items.append(doc)
    return {"ok": True, "count": len(items), "watchlist": items}


@router.post("/watchlist")
async def add_watchlist(
    body: WatchlistCreate,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Add a plate number + defendant ID to the active watchlist."""
    _require_staff(request, x_admin_key, x_admin_token)

    plate = _normalize_plate(body.plate_text)
    if not plate or len(plate) < 3:
        raise HTTPException(400, detail="Invalid plate_text")
    if not body.defendant_id.strip():
        raise HTTPException(400, detail="defendant_id required")

    now = datetime.now(timezone.utc)
    doc = {
        "plate_text": plate,
        "defendant_id": body.defendant_id.strip(),
        "defendant_name": body.defendant_name or "",
        "case_number": body.case_number or "",
        "notes": body.notes or "",
        "active": True,
        "source": "manual",
        "updated_at": now,
        "created_by": "api",
    }
    col = get_collection("lpr_watchlist")
    await col.update_one(
        {"plate_text": plate},
        {"$set": doc, "$setOnInsert": {"created_at": now, "first_seen_at": now}},
        upsert=True,
    )
    return {"ok": True, "watchlist": doc}


# ── Ad-hoc image scan ─────────────────────────────────────────────────────────

@router.post("/scan-image")
async def scan_image(
    request: Request,
    file: UploadFile = File(...),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Ad-hoc: scan an uploaded vehicle photo for plates."""
    _require_staff(request, x_admin_key, x_admin_token)

    from services.alpr_engine import get_alpr_engine, probe_alpr_deps

    deps = probe_alpr_deps()
    if not deps.get("engine_ready"):
        raise HTTPException(
            status_code=503,
            detail=(
                "ALPR vision stack not available on this host. "
                f"opencv={deps.get('opencv')} fast_alpr={deps.get('fast_alpr')} "
                f"error={deps.get('error')}. Use the alpr-worker image or install deps."
            ),
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, detail="Empty upload")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, detail="Image too large (max 15MB)")

    engine = get_alpr_engine()
    if not engine.ready:
        raise HTTPException(
            503,
            detail=f"ALPR engine failed to load: {engine.load_error}",
        )

    detections = engine.detect_bytes(data)
    return {
        "ok": True,
        "filename": file.filename,
        "count": len(detections),
        "detections": [d.to_dict() for d in detections],
    }
