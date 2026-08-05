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
  GET  /api/alpr/cameras/{id}/snapshot — staff-session proxy of FL511 JPEG (live feed UI)
"""
from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dashboard.auth.pin_middleware import get_session_from_request, session_is_admin
from dashboard.deps import get_collection

log = logging.getLogger("shamrock.alpr_api")

router = APIRouter(prefix="/api/alpr", tags=["alpr"])

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")
ALPR_ADMIN_KEY = os.getenv("ALPR_ADMIN_KEY") or os.getenv("OSINT_ADMIN_KEY") or DASHBOARD_PIN

# FL511 public traveler cameras only — never proxy arbitrary URLs.
_FL511_HOSTS = frozenset({"fl511.com", "www.fl511.com"})
_SNAPSHOT_UA = (
    "Mozilla/5.0 (compatible; ShamrockLeads-ALPR/1.0; +https://shamrockbailbonds.biz)"
)


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


def _find_camera(camera_id: str) -> Optional[Dict[str, Any]]:
    from services.alpr_cameras import load_camera_registry

    cid = str(camera_id or "").strip()
    if not cid:
        return None
    for cam in load_camera_registry():
        if str(cam.get("id") or "") == cid:
            return cam
    # Allow bare FL511 numeric ids
    bare = cid.removeprefix("fl511_")
    if bare != cid:
        for cam in load_camera_registry():
            if str(cam.get("id") or "") == f"fl511_{bare}":
                return cam
    return None


def _fl511_jpeg_url(cam: Dict[str, Any]) -> str:
    """Resolve a safe absolute FL511 JPEG snapshot URL for a camera."""
    raw = str(cam.get("stream_url") or "").strip()
    cid = str(cam.get("id") or "").removeprefix("fl511_")
    if raw.startswith("/"):
        raw = f"https://fl511.com{raw}"
    if not raw.startswith("http"):
        if cid:
            raw = f"https://fl511.com/map/Cctv/{cid}"
        else:
            return ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(raw).hostname or "").lower()
        if host not in _FL511_HOSTS:
            # Prefer known FL511 map endpoint over untrusted hosts
            if cid:
                return f"https://fl511.com/map/Cctv/{cid}"
            return ""
    except Exception:
        if cid:
            return f"https://fl511.com/map/Cctv/{cid}"
        return ""
    return raw


def _registry_stream_list(registry: List[Dict[str, Any]], worker_streams: Any) -> List[Dict[str, Any]]:
    """Merge worker telemetry with registry so the UI always has stream_url + View Feed."""
    by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(worker_streams, dict):
        for s in worker_streams.get("streams") or []:
            if isinstance(s, dict) and s.get("id"):
                by_id[str(s["id"])] = dict(s)
    elif isinstance(worker_streams, list):
        for s in worker_streams:
            if isinstance(s, dict) and s.get("id"):
                by_id[str(s["id"])] = dict(s)

    out: List[Dict[str, Any]] = []
    for cam in registry:
        if not cam.get("enabled"):
            continue
        cid = str(cam.get("id") or "")
        if not cid:
            continue
        tel = by_id.get(cid) or {}
        jpeg = _fl511_jpeg_url(cam)
        out.append(
            {
                "id": cid,
                "name": cam.get("name") or cid,
                "county": cam.get("county") or "",
                "connected": bool(tel.get("connected")),
                "consecutive_failures": tel.get("consecutive_failures", 0),
                "frames_ok": tel.get("frames_ok", 0),
                "frames_fail": tel.get("frames_fail", 0),
                "last_frame_at": tel.get("last_frame_at"),
                "last_error": tel.get("last_error"),
                "stream_type": cam.get("stream_type") or tel.get("stream_type") or "jpeg",
                "stream_url": jpeg or tel.get("stream_url") or "",
                "video_url": cam.get("video_url") or "",
            }
        )
    # Worker-only cameras not in registry
    for cid, tel in by_id.items():
        if any(x["id"] == cid for x in out):
            continue
        out.append(tel)
    return out


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
    # Always attach a full stream list (registry ∪ worker) so View Feed never lacks URLs
    stream_list = _registry_stream_list(enabled, streams)
    streams_out: Dict[str, Any]
    if isinstance(streams, dict):
        streams_out = dict(streams)
        streams_out["streams"] = stream_list
        streams_out["cameras_enabled"] = len(enabled)
        if streams_out.get("cameras_connected") is None:
            streams_out["cameras_connected"] = sum(1 for s in stream_list if s.get("connected"))
    else:
        streams_out = {
            "streams": stream_list,
            "cameras_enabled": len(enabled),
            "cameras_connected": sum(1 for s in stream_list if s.get("connected")),
        }

    return {
        "ok": True,
        "service": "alpr",
        "deps": deps,
        "cameras_registered": len(registry),
        "cameras_enabled": len(enabled),
        "cameras_connected": streams_out.get("cameras_connected"),
        "worker": {
            "engine_ready": worker_doc.get("engine_ready"),
            "engine_error": worker_doc.get("engine_error"),
            "cycle": worker_doc.get("cycle"),
            "hits_total": worker_doc.get("hits_total"),
            "updated_at": worker_doc.get("updated_at"),
            "last_error": worker_doc.get("last_error"),
            "streams": streams_out,
        },
        "hits_last_24h": hits_24h,
        "watchlist_count": watch_count,
        "env": {
            "alpr_enabled": os.getenv("ALPR_ENABLED", "true"),
            "frame_interval_s": os.getenv("ALPR_FRAME_INTERVAL_S", "2.5"),
        },
    }


@router.get("/cameras/{camera_id}/snapshot")
async def alpr_camera_snapshot(
    camera_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Proxy a single FL511 JPEG frame for the live-feed modal.

    Same-origin + staff session so <img> never navigates the SPA to /login
    (relative /map/Cctv/* paths would hit PinAuthMiddleware and redirect).
    """
    _require_staff(request, x_admin_key, x_admin_token)

    cam = _find_camera(camera_id)
    if not cam:
        # Still allow bare FL511 ids that aren't in the local registry
        bare = str(camera_id).removeprefix("fl511_").strip()
        if not bare.isdigit():
            raise HTTPException(status_code=404, detail="Camera not found")
        jpeg_url = f"https://fl511.com/map/Cctv/{bare}"
        cam_name = f"FL511 #{bare}"
    else:
        jpeg_url = _fl511_jpeg_url(cam)
        cam_name = cam.get("name") or camera_id

    if not jpeg_url:
        raise HTTPException(status_code=404, detail="No FL511 snapshot URL for camera")

    req = urllib.request.Request(
        jpeg_url,
        headers={
            "User-Agent": _SNAPSHOT_UA,
            "Accept": "image/jpeg,image/*,*/*",
            "Referer": "https://fl511.com/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
            content_type = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    except urllib.error.HTTPError as exc:
        log.warning("FL511 snapshot HTTP %s for %s: %s", exc.code, camera_id, cam_name)
        raise HTTPException(status_code=502, detail=f"FL511 returned HTTP {exc.code}") from exc
    except Exception as exc:
        log.warning("FL511 snapshot failed for %s: %s", camera_id, exc)
        raise HTTPException(status_code=502, detail=f"Failed to fetch FL511 frame: {exc}") from exc

    if not data:
        raise HTTPException(status_code=502, detail="Empty frame from FL511")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=502, detail="Frame too large")

    # Guard: FL511 occasionally returns HTML error pages
    if data[:1] == b"<" or data[:15].lower().startswith(b"<!doctype"):
        raise HTTPException(status_code=502, detail="FL511 returned HTML instead of image")

    return Response(
        content=data,
        media_type=content_type if content_type.startswith("image/") else "image/jpeg",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-ALPR-Camera": str(camera_id)[:80],
            "X-ALPR-Source": "fl511",
        },
    )


@router.post("/refresh-cameras")
async def alpr_refresh_cameras(
    request: Request,
    counties: Optional[List[str]] = Body(None),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Trigger live FL511 camera resolution & update config/alpr_cameras.json."""
    _require_staff(request, x_admin_key, x_admin_token)
    try:
        from services.fl511_camera_resolver import resolve_and_save_swfl_cameras
        cams = resolve_and_save_swfl_cameras(counties=counties)
        return {
            "ok": True,
            "resolved_count": len(cams),
            "counties": counties or ["Lee", "Collier", "Charlotte", "Hendry", "Sarasota", "Manatee", "Hillsborough", "Pinellas", "Orange", "Miami-Dade"],
            "cameras": cams[:10],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to refresh FL511 cameras: {exc}")


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
