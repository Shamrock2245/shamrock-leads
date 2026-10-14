"""Never print a Miami-Dade internal booking key in a rendered page or download.

Owner exception 2026-10-09: Miami-Dade docs store an internal natural key
(``md_dedupe_v2:<sha256>``) in ``booking_number`` so the unique index and
dashboard routing keep working. JSON API payloads keep it as the routing id
(clients print ``booking_number_display`` / ``slBookingLabel``) and are never
touched here.

Scope (deliberately narrow, for safety and speed):
  * Bodies are rewritten only for ``text/html``, ``text/csv`` and
    ``text/plain`` responses sent as a single message (HTMLResponse,
    PlainTextResponse, Response). ``Content-Length`` is recomputed only when the
    body actually changed.
  * Bodies over ``MAX_REWRITE_BYTES`` (2 MB) are passed through unchanged and
    logged (content type + size only).
  * Streaming / multi-chunk responses (StreamingResponse, FileResponse, SSE)
    pass through untouched; CSV streamers already write through
    ``redacting_csv_writer``.
  * Only the ``Content-Disposition`` header (download filename) is rewritten;
    ``Location`` and every other header are left alone so redirects to a record
    keep working.
  * A body without the ``md_dedupe_v`` marker is forwarded as the same bytes
    object (one substring scan, no copy).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.booking_identity import _ANY_INTERNAL_KEY_RE

logger = logging.getLogger(__name__)

MAX_REWRITE_BYTES = 2 * 1024 * 1024
_MARK = b"md_dedupe_v"
_REWRITE_TYPES = (b"text/html", b"text/csv", b"text/plain")
_KEY_BYTES_RE = re.compile(_ANY_INTERNAL_KEY_RE.pattern.encode())


def redact_bytes(data: bytes) -> bytes:
    """Same object back when there is nothing to redact (byte-identical pass-through)."""
    return _KEY_BYTES_RE.sub(b"", data) if _MARK in data else data


def _is_rewritable(content_type: bytes) -> bool:
    ctype = content_type.split(b";", 1)[0].strip().lower()
    return ctype in _REWRITE_TYPES


class BookingKeyRedactMiddleware:
    def __init__(self, app: Any, max_bytes: int = MAX_REWRITE_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state: dict = {"start": None, "pending": False}

        async def _send(message: dict) -> None:
            mtype = message.get("type")
            if mtype == "http.response.start":
                headers = message.get("headers", [])
                if any(k.lower() == b"content-disposition" and _MARK in v for k, v in headers):
                    headers = [(k, redact_bytes(v)) if k.lower() == b"content-disposition" else (k, v)
                               for k, v in headers]
                    message = dict(message, headers=headers)
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if _is_rewritable(ctype):
                    state["start"], state["pending"] = message, True  # hold until the first body
                    return
                await send(message)
                return
            if mtype == "http.response.body" and state["pending"]:
                start = state["start"]
                state["pending"] = False
                body = message.get("body", b"") or b""
                if message.get("more_body", False):
                    await send(start)  # streamed: untouched
                    await send(message)
                    return
                if len(body) > self.max_bytes:
                    log = logger.warning if _MARK in body else logger.debug
                    log("[booking-key] body over %d bytes not rewritten (len=%d)", self.max_bytes, len(body))
                    await send(start)
                    await send(message)
                    return
                new_body = redact_bytes(body)
                if new_body is not body:
                    headers = [(k, str(len(new_body)).encode()) if k.lower() == b"content-length" else (k, v)
                               for k, v in start["headers"]]
                    start = dict(start, headers=headers)
                    message = dict(message, body=new_body)
                await send(start)
                await send(message)
                return
            await send(message)

        await self.app(scope, receive, _send)
