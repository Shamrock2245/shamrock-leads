#!/usr/bin/env python3
"""Fetch relay for Brendan's Mac. Headed Patchright Chromium, not urllib.

Live check on the office Mac (2026-11-13): plain urllib and headless Chromium
both get HTTP 403 from Cloudflare. A headed Patchright window gets HTTP 200.
This process keeps one browser and one context open, and serves requests one
at a time. It does not call a CAPTCHA solver.

The Mac must be logged into a GUI session, because headed Chromium opens a
window. Install it as a user LaunchAgent (``~/Library/LaunchAgents``), not a
system LaunchDaemon. Keep the Mac awake, VPN off, Tailscale up.

    SHAMROCK_MAC_RELAY_TOKEN=<same value as the VPS> \\
        python scripts/mac_roster_relay.py --bind 100.x.x.x --port 8765

``--headless`` exists and defaults off. A missing token refuses to start.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.access_strategy import RELAY_TOKEN_ENV, relay_target  # noqa: E402

MAX_BODY = 2_000_000
CF_WAIT_S = 25.0
CF_POLL_S = 0.4
_CHALLENGE_TITLES = ("just a moment", "attention required")
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


def title_is_challenged(title: str) -> bool:
    """Cloudflare interstitial titles seen on the office Mac."""
    folded = " ".join(str(title or "").split()).casefold()
    return any(marker in folded for marker in _CHALLENGE_TITLES)


def clip_body(html: str) -> str:
    text = html or ""
    if len(text) > MAX_BODY:
        return text[:MAX_BODY]
    return text


def read_cleared_page(page, url: str, *, timeout_s: float, sleep: Callable[[float], None]) -> Tuple[int, str]:
    """Open ``url`` on an existing page and wait out the Cloudflare title.

    A cleared page returns 200 and ``page.content()``. A title that is still
    ``Just a moment`` or ``Attention Required`` when the wait ends returns a
    non-200 status so the VPS stays fail_closed. The HTML is clipped to
    ``MAX_BODY`` and is not logged.
    """
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    status = getattr(resp, "status", None) if resp is not None else None
    deadline = time.monotonic() + timeout_s
    title = page.title()
    while title_is_challenged(title):
        if time.monotonic() >= deadline:
            code = status if isinstance(status, int) and status >= 400 else 403
            return int(code), clip_body(page.content())
        sleep(CF_POLL_S)
        title = page.title()
    return 200, clip_body(page.content())


class HeadedBrowser:
    """One Patchright Chromium, one persistent context, one page.

    HTTP handlers may run on different threads. The browser stays on a single
    worker thread, and a lock makes each page read finish before the next
    starts. Patchright is imported only when a real browser is opened.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        page_factory: Optional[Callable[[], object]] = None,
        timeout_s: float = CF_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.headless = headless
        self._page_factory = page_factory
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pw = None
        self._context = None
        self._page = None
        self._jobs: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="mac-relay-browser", daemon=True)
        self._worker.start()

    def fetch(self, url: str) -> Tuple[int, str]:
        done = threading.Event()
        box: dict = {"done": done}
        self._jobs.put((url, box))
        done.wait()
        if box.get("error") is not None:
            raise box["error"]
        return box["result"]

    def _run(self) -> None:
        while True:
            url, box = self._jobs.get()
            try:
                with self._lock:
                    if self._page is None:
                        self._page = self._open()
                    box["result"] = read_cleared_page(
                        self._page, url, timeout_s=self._timeout_s, sleep=self._sleep,
                    )
            except Exception as exc:  # noqa: BLE001 - returned to the request thread
                box["error"] = exc
            finally:
                box["done"].set()

    def _open(self):
        if self._page_factory is not None:
            return self._page_factory()
        from patchright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # Headed by default. The persistent context keeps the clearance cookie
        # for later requests in this process.
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=os.path.expanduser("~/.shamrock-mac-relay"),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
        )
        pages = self._context.pages
        return pages[0] if pages else self._context.new_page()


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
            http_code = 200 if status == 200 else (status if status >= 400 else 502)
            self._send(http_code, {"status": status, "body": body})

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


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Owner Mac roster relay")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chromium headless. Default is headed, which is what clears Cloudflare on the office Mac.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.getenv(RELAY_TOKEN_ENV, "").strip()
    if not token:
        print(json.dumps({"result": "error", "error": "SHAMROCK_MAC_RELAY_TOKEN is not set"}))
        return 1
    browser = HeadedBrowser(headless=args.headless)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(token, browser.fetch))
    print(json.dumps({
        "result": "listening", "bind": args.bind, "port": args.port, "headless": args.headless,
    }))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
