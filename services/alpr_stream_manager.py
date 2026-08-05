"""
Camera stream manager — sample frames from FL511 HLS / JPEG streams.

Uses OpenCV VideoCapture when available; falls back to HTTP GET for JPEG URLs.
Auto-reconnects on dropped streams with exponential backoff.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.alpr_cameras import enabled_cameras, load_camera_registry

logger = logging.getLogger(__name__)

FRAME_INTERVAL_S = float(os.getenv("ALPR_FRAME_INTERVAL_S", "2.5"))
CONNECT_TIMEOUT_S = float(os.getenv("ALPR_STREAM_CONNECT_TIMEOUT_S", "15"))
READ_TIMEOUT_S = float(os.getenv("ALPR_STREAM_READ_TIMEOUT_S", "20"))
MAX_BACKOFF_S = float(os.getenv("ALPR_STREAM_MAX_BACKOFF_S", "120"))


@dataclass
class StreamState:
    camera_id: str
    name: str
    stream_url: str
    stream_type: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    county: str = ""
    capture: Any = None  # cv2.VideoCapture
    consecutive_failures: int = 0
    last_frame_at: float = 0.0
    last_error: Optional[str] = None
    frames_ok: int = 0
    frames_fail: int = 0
    connected: bool = False

    def backoff_s(self) -> float:
        return min(MAX_BACKOFF_S, (2 ** min(self.consecutive_failures, 6)) * 1.5)


class ALPRStreamManager:
    """Maintain FL511 camera connections and yield sampled frames."""

    def __init__(self, cameras: Optional[List[Dict[str, Any]]] = None):
        registry = cameras if cameras is not None else load_camera_registry()
        self.registry = registry
        self.streams: Dict[str, StreamState] = {}
        for cam in enabled_cameras(registry):
            self.streams[cam["id"]] = StreamState(
                camera_id=cam["id"],
                name=cam["name"],
                stream_url=cam["stream_url"],
                stream_type=cam.get("stream_type") or "jpeg",
                lat=cam.get("lat"),
                lon=cam.get("lon"),
                county=str(cam.get("county") or ""),
            )
        self.frame_interval_s = FRAME_INTERVAL_S
        logger.info(
            "ALPRStreamManager: %d enabled cameras (interval=%.1fs)",
            len(self.streams),
            self.frame_interval_s,
        )

    def status(self) -> Dict[str, Any]:
        active = sum(1 for s in self.streams.values() if s.connected)
        return {
            "cameras_registered": len(self.registry),
            "cameras_enabled": len(self.streams),
            "cameras_connected": active,
            "frame_interval_s": self.frame_interval_s,
            "streams": [
                {
                    "id": s.camera_id,
                    "name": s.name,
                    "connected": s.connected,
                    "consecutive_failures": s.consecutive_failures,
                    "frames_ok": s.frames_ok,
                    "frames_fail": s.frames_fail,
                    "last_frame_at": s.last_frame_at or None,
                    "last_error": s.last_error,
                    "stream_type": s.stream_type,
                }
                for s in self.streams.values()
            ],
        }

    def close_all(self) -> None:
        for s in self.streams.values():
            self._close_capture(s)

    def _close_capture(self, state: StreamState) -> None:
        cap = state.capture
        state.capture = None
        state.connected = False
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _open_capture(self, state: StreamState) -> bool:
        self._close_capture(state)
        url = state.stream_url
        if not url:
            state.last_error = "empty stream_url"
            return False
        try:
            import cv2

            # OpenCV ffmpeg backend for HLS
            cap = cv2.VideoCapture(url)
            # Shorter timeouts where supported
            try:
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(CONNECT_TIMEOUT_S * 1000))
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(READ_TIMEOUT_S * 1000))
            except Exception:
                pass
            if not cap.isOpened():
                state.last_error = "VideoCapture failed to open"
                state.consecutive_failures += 1
                return False
            state.capture = cap
            state.connected = True
            state.consecutive_failures = 0
            state.last_error = None
            logger.info("Stream open: %s (%s)", state.camera_id, state.stream_type)
            return True
        except Exception as exc:
            state.last_error = str(exc)[:200]
            state.consecutive_failures += 1
            logger.warning("Stream open failed %s: %s", state.camera_id, exc)
            return False

    def _read_jpeg_http(self, state: StreamState) -> Optional[Any]:
        """Fetch a single JPEG snapshot over HTTP."""
        try:
            import cv2
            import numpy as np
            import urllib.request

            req = urllib.request.Request(
                state.stream_url,
                headers={
                    "User-Agent": "ShamrockLeads-ALPR/1.0 (+https://leads.shamrockbailbonds.biz)",
                    "Accept": "image/*,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT_S) as resp:
                data = resp.read()
            if not data:
                raise RuntimeError("empty JPEG body")
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError("cv2.imdecode failed")
            return img
        except Exception as exc:
            state.last_error = str(exc)[:200]
            state.consecutive_failures += 1
            state.connected = False
            state.frames_fail += 1
            return None

    def grab_frame(self, state: StreamState) -> Optional[Any]:
        """
        Grab one frame from a camera stream.

        Returns BGR numpy image or None on failure (with reconnect scheduled).
        """
        now = time.time()
        # Respect per-camera backoff after failures
        if state.consecutive_failures and (
            now - state.last_frame_at < state.backoff_s()
            and not state.connected
        ):
            return None

        stype = (state.stream_type or "jpeg").lower()

        # Prefer HTTP JPEG for snapshot endpoints (cheaper than full HLS)
        if stype == "jpeg" or state.stream_url.lower().endswith(
            (".jpg", ".jpeg", ".png")
        ):
            img = self._read_jpeg_http(state)
            if img is not None:
                state.connected = True
                state.consecutive_failures = 0
                state.frames_ok += 1
                state.last_frame_at = now
                state.last_error = None
            return img

        # HLS / MP4 via OpenCV
        if state.capture is None or not state.connected:
            if not self._open_capture(state):
                state.last_frame_at = now
                return None

        try:
            cap = state.capture
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("frame read failed / stream ended")
            state.frames_ok += 1
            state.consecutive_failures = 0
            state.last_frame_at = now
            state.connected = True
            state.last_error = None
            return frame
        except Exception as exc:
            state.last_error = str(exc)[:200]
            state.frames_fail += 1
            state.consecutive_failures += 1
            state.last_frame_at = now
            logger.warning(
                "Stream read fail %s (fail#%d): %s — reconnecting",
                state.camera_id,
                state.consecutive_failures,
                exc,
            )
            self._close_capture(state)
            # Immediate reconnect attempt once
            if state.consecutive_failures <= 2:
                self._open_capture(state)
            return None

    def iter_due_frames(self) -> List[Tuple[StreamState, Any]]:
        """
        Return list of (state, frame) for cameras due for sampling.

        Call this in the worker loop; sleep externally between cycles.
        """
        now = time.time()
        out: List[Tuple[StreamState, Any]] = []
        for state in self.streams.values():
            if state.last_frame_at and (now - state.last_frame_at) < self.frame_interval_s:
                # Still throttle successes; allow faster retry only after failure backoff
                if state.connected:
                    continue
            frame = self.grab_frame(state)
            if frame is not None:
                out.append((state, frame))
        return out
