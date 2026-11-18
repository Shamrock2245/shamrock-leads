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
# Exception text is not logged. Playwright and Chromium messages can carry a
# page snippet, a local path, or other personal data. The class name is enough.
_DETAIL_CODE = "omitted"
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


def _redaction_marker(secret: str) -> str:
    """A stand-in that does not contain ``secret``.

    ``[redacted]`` is the usual marker. If the token is a substring of that
    word (``redacted``, ``act``, ``[``), appending characters never removes the
    match, so the marker is eight copies of a character the token does not
    contain. The scans are bounded.
    """
    plain = "[redacted]"
    if secret not in plain:
        return plain
    for start, stop in ((48, 58), (65, 91), (97, 123), (32, 127)):
        for code in range(start, stop):
            ch = chr(code)
            if ch not in secret:
                return ch * 8
    return "\ufffd" * 8


def _redact_secret(text: str, secret: str) -> str:
    """Replace ``secret`` so a stderr line cannot echo the relay token."""
    if not secret or secret not in text:
        return text
    return text.replace(secret, _redaction_marker(secret))


def _allowlisted_county(county: str, state: str) -> str:
    """Canonical allowlisted county, or ``unknown`` so the raw query is not logged."""
    name = " ".join(str(county or "").split())
    state_code = " ".join(str(state or "").split()).upper()
    if relay_target(name, state_code):
        return name
    return "unknown"


def _write_stderr(line: str) -> None:
    sys.stderr.write(line if line.endswith("\n") else line + "\n")
    try:
        sys.stderr.flush()
    except Exception:
        pass


def log_fetch_failed(county: str, state: str, exc: BaseException, token: str) -> None:
    """One stderr line when ``read_allowlisted`` raises.

    The line carries the allowlisted county and the exception class. ``detail``
    is the fixed code ``omitted``: ``str(exc)`` is not written. The line does
    not include the token, request headers, the request line, or the page body.
    The HTTP payload stays ``fetch_failed`` plus ``error_type``.
    """
    label = _redact_secret(_allowlisted_county(county, state), token)
    error_type = _redact_secret(type(exc).__name__, token)
    detail = _redact_secret(_DETAIL_CODE, token)
    _write_stderr(
        f"[mac-relay] fetch_failed county={label} error_type={error_type} detail={detail}"
    )


def log_browser_worker(started: bool, exc: BaseException | None = None) -> None:
    """One stderr line after the browser worker thread starts, or fails to."""
    if started:
        _write_stderr("[mac-relay] browser_worker started")
        return
    token = os.getenv(RELAY_TOKEN_ENV, "")
    error_type = _redact_secret(type(exc).__name__ if exc is not None else "Exception", token)
    detail = _redact_secret(_DETAIL_CODE, token)
    _write_stderr(
        f"[mac-relay] browser_worker failed error_type={error_type} detail={detail}"
    )


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
    saw_challenge = title_is_challenged(title)
    while title_is_challenged(title):
        if time.monotonic() >= deadline:
            code = status if isinstance(status, int) and status >= 400 else 403
            return int(code), clip_body(page.content())
        sleep(CF_POLL_S)
        title = page.title()
    html = clip_body(page.content())
    # A cleared interstitial may report 200. A real 404, 429, or 5xx whose
    # title is not a challenge must keep that status.
    if not saw_challenge and isinstance(status, int) and status >= 400:
        return int(status), html
    return 200, html


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
        try:
            self._worker.start()
        except Exception as exc:
            log_browser_worker(False, exc)
            raise
        log_browser_worker(True)

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
                if self._browser_state_dead(exc):
                    self._reset_browser()
            finally:
                box["done"].set()

    def _browser_state_dead(self, exc: BaseException | None = None) -> bool:
        """True when the cached page or context cannot serve another request."""
        if exc is not None:
            name = type(exc).__name__.casefold()
            text = str(exc).casefold()
            if "closed" in name or "has been closed" in text or "target closed" in text:
                return True
        page = self._page
        if page is not None:
            checker = getattr(page, "is_closed", None)
            if callable(checker):
                try:
                    if checker():
                        return True
                except Exception:
                    return True
        context = self._context
        if context is not None:
            checker = getattr(context, "is_closed", None)
            if callable(checker):
                try:
                    if checker():
                        return True
                except Exception:
                    return True
        return False

    def _reset_browser(self) -> None:
        for closer in (self._context, self._pw):
            if closer is None:
                continue
            stop = getattr(closer, "close", None) or getattr(closer, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        self._page = None
        self._context = None
        self._pw = None

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
                log_fetch_failed(county, state, exc, expected_token)
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
