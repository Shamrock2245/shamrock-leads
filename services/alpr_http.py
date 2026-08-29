"""Tiny internal HTTP API for the ALPR worker (health + ad-hoc image scan)."""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

logger = logging.getLogger(__name__)

ALPR_HTTP_PORT = int(os.getenv("ALPR_HTTP_PORT", "8090"))

_engine_ref: Any = None
_server: Optional[ThreadingHTTPServer] = None


def attach_engine(engine: Any) -> None:
    global _engine_ref
    _engine_ref = engine


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("alpr-http " + fmt, *args)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in ("/health", "/status"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        from services.alpr_engine import probe_alpr_deps

        deps = probe_alpr_deps()
        eng = _engine_ref
        ready = bool(eng is not None and getattr(eng, "ready", False))
        self._json(200 if (deps.get("engine_ready") or ready) else 503, {
            "ok": True,
            "service": "alpr-worker",
            "engine_ready": ready or bool(deps.get("engine_ready")),
            "engine_error": (getattr(eng, "load_error", None) if eng else None) or deps.get("error"),
            "deps": deps,
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/scan":
            self._json(404, {"ok": False, "error": "not found"})
            return
        eng = _engine_ref
        if eng is None or not getattr(eng, "ready", False):
            self._json(503, {"ok": False, "error": "ALPR engine not ready on worker"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 15 * 1024 * 1024:
            self._json(400, {"ok": False, "error": "image required (max 15MB)"})
            return
        data = self.rfile.read(length)
        try:
            dets = eng.detect_bytes(data)
        except Exception as exc:
            logger.warning("alpr-http scan failed: %s", exc)
            self._json(500, {"ok": False, "error": "scan failed"})
            return
        self._json(200, {
            "ok": True,
            "count": len(dets),
            "detections": [d.to_dict() for d in dets],
        })


def start_http_server(engine: Any, port: int = ALPR_HTTP_PORT) -> None:
    """Daemon thread — internal Docker network only."""
    global _server
    attach_engine(engine)
    try:
        _server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError as exc:
        logger.warning("ALPR HTTP server not started on :%s: %s", port, exc)
        return
    t = threading.Thread(target=_server.serve_forever, name="alpr-http", daemon=True)
    t.start()
    logger.info("ALPR HTTP listening on :%s (health + /scan)", port)
