"""Never print a Miami-Dade internal booking key in a rendered page or file.

Owner exception 2026-10-09: Miami-Dade docs store an internal natural key
(``md_dedupe_v2:<sha256>``) in ``booking_number`` so the unique index and
dashboard routing keep working. JSON API payloads keep it as the routing id
(clients render ``booking_number_display`` / ``slBookingLabel``). Everything a
person reads directly — server-rendered HTML (PIN/client portals, check-in
pages), text and CSV downloads, and every response header such as a
``Content-Disposition`` filename — passes through this ASGI middleware, which
removes key substrings.

Single-message bodies (HTMLResponse, PlainTextResponse, Response) are
redacted and Content-Length is recomputed. Multi-chunk streams pass through
unchanged: the CSV streamers already write through ``redacting_csv_writer``,
SSE (``text/event-stream``) carries routing ids for the client, and static
files contain no keys.
"""
from __future__ import annotations

import re
from typing import Any

from core.booking_identity import _ANY_INTERNAL_KEY_RE

_MARK = b"md_dedupe_v"
_TEXT_TYPES = (b"text/html", b"text/plain", b"text/csv", b"application/csv",
               b"text/calendar", b"application/xml", b"text/xml")
_KEY_BYTES_RE = re.compile(_ANY_INTERNAL_KEY_RE.pattern.encode())


def redact_bytes(data: bytes) -> bytes:
    return _KEY_BYTES_RE.sub(b"", data) if _MARK in data else data


class BookingKeyRedactMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state: dict = {"start": None, "text": False, "done": False}

        async def _send(message: dict) -> None:
            mtype = message.get("type")
            if mtype == "http.response.start":
                headers = [(k, redact_bytes(v)) for k, v in message.get("headers", [])]
                ctype = b""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        ctype = v.lower()
                state["text"] = any(ctype.startswith(t) for t in _TEXT_TYPES)
                state["start"] = dict(message, headers=headers)
                if not state["text"]:
                    await send(state["start"])
                    state["done"] = True
                return
            if mtype == "http.response.body" and not state["done"]:
                start = state["start"]
                state["done"] = True
                if message.get("more_body", False):
                    await send(start)  # streamed: rows are redacted at the writer
                    await send(message)
                    return
                body = message.get("body", b"") or b""
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
