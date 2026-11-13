#!/usr/bin/env python3
"""Tiny fetch relay for Brendan's Mac. Stdlib only.

Leads Ops runs this on the Mac (home ISP or iPhone hotspot, VPN off), bound to
the Mac's Tailscale address. The VPS calls it with ``SHAMROCK_MAC_RELAY_URL``
and ``SHAMROCK_MAC_RELAY_TOKEN``. It fetches only the Manatee bookings page and
the Sarasota listing. It is not an open proxy.

    SHAMROCK_MAC_RELAY_TOKEN=<same value as the VPS> \\
        python scripts/mac_roster_relay.py --bind 100.x.x.x --port 8765

The process must stay up (launchd KeepAlive) and the Mac must stay awake.
A missing token refuses to start.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Tuple
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.access_strategy import RELAY_TOKEN_ENV, relay_target  # noqa: E402

USER_AGENT = (
    "ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; "
    "owner Mac residential relay)"
)
MAX_BODY = 2_000_000
Fetch = Callable[[str], Tuple[int, str]]


def authorized(presented: str, expected: str) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def read_allowlisted(county: str, state: str, fetch: Fetch) -> Tuple[int, str]:
    """Fetch the allowlisted page, or (404, '') when the county is not one of the two."""
    target = relay_target(county, state)
    if not target:
        return 404, ""
    return fetch(target)


def default_fetch(url: str) -> Tuple[int, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(req, timeout=45) as resp:  # noqa: S310 - allowlisted https only
        raw = resp.read(MAX_BODY + 1)
        status = getattr(resp, "status", 200) or 200
    if len(raw) > MAX_BODY:
        raw = raw[:MAX_BODY]
    return int(status), raw.decode("utf-8", "replace")


def make_handler(expected_token: str, fetch: Fetch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/fetch":
                self._send(404, {"error": "not_found"})
                return
            presented = self.headers.get("X-Shamrock-Relay-Token", "")
            if not authorized(presented, expected_token):
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(parsed.query)
            county = (qs.get("county") or [""])[0]
            state = (qs.get("state") or [""])[0]
            try:
                status, body = read_allowlisted(county, state, fetch)
            except Exception as exc:
                self._send(502, {"error": "fetch_failed", "error_type": type(exc).__name__})
                return
            if status == 404 and not body:
                self._send(404, {"error": "county_not_allowlisted"})
                return
            self._send(200, {"status": status, "body": body})

        def log_message(self, fmt, *args):
            # Do not log the request line: it can carry the county page, and
            # the token is a header we never want in an access log.
            sys.stderr.write("[mac-relay] request\n")

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner Mac roster relay")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    token = os.getenv(RELAY_TOKEN_ENV, "").strip()
    if not token:
        print(json.dumps({"result": "error", "error": "SHAMROCK_MAC_RELAY_TOKEN is not set"}))
        return 1
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(token, default_fetch))
    print(json.dumps({"result": "listening", "bind": args.bind, "port": args.port}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
