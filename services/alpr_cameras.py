"""
FL511 / SWFL traffic camera registry for ALPR.

Stream URLs change over time — override via env ``ALPR_CAMERAS_JSON`` (JSON array)
or drop a file at ``ALPR_CAMERAS_FILE`` (default ``config/alpr_cameras.json``).

Each camera:
  {
    "id": "fl511_i75_mm136",
    "name": "I-75 @ Colonial Blvd (MM 136)",
    "county": "Lee",
    "stream_url": "https://...",
    "stream_type": "hls" | "jpeg" | "mp4",
    "lat": 26.6042,
    "lon": -81.8214,
    "enabled": true
  }
"""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Seed registry — SWFL corridors (placeholders; replace with live FL511 URLs).
# FL511 traveler info: https://fl511.com/ — camera feeds are public DOT assets.
_DEFAULT_CAMERAS: List[Dict[str, Any]] = [
    {
        "id": "fl511_i75_mm136",
        "name": "I-75 @ Colonial Blvd (MM 136)",
        "county": "Lee",
        "stream_url": os.getenv(
            "ALPR_CAM_I75_COLONIAL",
            "https://fl511.com/map/Cctv/136--1",
        ),
        "stream_type": "jpeg",
        "lat": 26.6042,
        "lon": -81.8214,
        "enabled": True,
    },
    {
        "id": "fl511_us41_mlk",
        "name": "US-41 @ MLK Blvd",
        "county": "Lee",
        "stream_url": os.getenv("ALPR_CAM_US41_MLK", ""),
        "stream_type": "jpeg",
        "lat": 26.6406,
        "lon": -81.8723,
        "enabled": False,
    },
    {
        "id": "fl511_midpoint_bridge",
        "name": "Midpoint Bridge (Cape Coral)",
        "county": "Lee",
        "stream_url": os.getenv("ALPR_CAM_MIDPOINT", ""),
        "stream_type": "hls",
        "lat": 26.6400,
        "lon": -81.9100,
        "enabled": False,
    },
    {
        "id": "fl511_cape_coral_bridge",
        "name": "Cape Coral Bridge",
        "county": "Lee",
        "stream_url": os.getenv("ALPR_CAM_CAPE_BRIDGE", ""),
        "stream_type": "hls",
        "lat": 26.5620,
        "lon": -81.9420,
        "enabled": False,
    },
    {
        "id": "fl511_i75_collier",
        "name": "I-75 Collier County corridor",
        "county": "Collier",
        "stream_url": os.getenv("ALPR_CAM_I75_COLLIER", ""),
        "stream_type": "jpeg",
        "lat": 26.1420,
        "lon": -81.5710,
        "enabled": False,
    },
    {
        "id": "fl511_i75_charlotte",
        "name": "I-75 Charlotte County corridor",
        "county": "Charlotte",
        "stream_url": os.getenv("ALPR_CAM_I75_CHARLOTTE", ""),
        "stream_type": "jpeg",
        "lat": 26.9340,
        "lon": -81.9530,
        "enabled": False,
    },
]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_camera_registry() -> List[Dict[str, Any]]:
    """Load cameras from env JSON, file, or built-in SWFL seed list."""
    raw_json = (os.getenv("ALPR_CAMERAS_JSON") or "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
            if isinstance(data, list) and data:
                return [_normalize_cam(c) for c in data if isinstance(c, dict)]
        except json.JSONDecodeError as exc:
            logger.warning("ALPR_CAMERAS_JSON invalid: %s", exc)

    path = Path(
        os.getenv(
            "ALPR_CAMERAS_FILE",
            str(_project_root() / "config" / "alpr_cameras.json"),
        )
    )
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                logger.info("Loaded %d ALPR cameras from %s", len(data), path)
                return [_normalize_cam(c) for c in data if isinstance(c, dict)]
        except Exception as exc:
            logger.warning("Failed reading ALPR cameras file %s: %s", path, exc)

    cams = [_normalize_cam(c) for c in deepcopy(_DEFAULT_CAMERAS)]
    logger.info(
        "Using default ALPR camera seed (%d entries; enable/set URLs for live feeds)",
        len(cams),
    )
    return cams


def _normalize_cam(cam: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cam)
    out["id"] = str(out.get("id") or "cam_unknown")
    out["name"] = str(out.get("name") or out["id"])
    out["stream_url"] = str(out.get("stream_url") or "").strip()
    out["stream_type"] = str(out.get("stream_type") or "jpeg").lower()
    out["enabled"] = bool(out.get("enabled", True)) and bool(out["stream_url"])
    try:
        out["lat"] = float(out["lat"]) if out.get("lat") is not None else None
    except (TypeError, ValueError):
        out["lat"] = None
    try:
        out["lon"] = float(out["lon"]) if out.get("lon") is not None else None
    except (TypeError, ValueError):
        out["lon"] = None
    return out


def enabled_cameras(registry: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    cams = registry if registry is not None else load_camera_registry()
    return [c for c in cams if c.get("enabled") and c.get("stream_url")]
