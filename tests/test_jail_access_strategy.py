"""Manatee jail and Sarasota jail fetch strategy. Fixtures only. No network.

Owner directive 2026-11-13: Mac relay first, then stealth browser + solver,
then fail closed. Both counties stay SOURCE_CONTRACT_VALIDATED False.
Hillsborough never receives a solver.
"""
from __future__ import annotations

from scrapers.access_strategy import (
    FAIL_CLOSED,
    MAC_RELAY,
    PLAIN,
    RELAY_TOKEN_ENV,
    RELAY_URL_ENV,
    SOLVER_ENV,
    STEALTH_SOLVER,
    plan_for,
    run_access,
)
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.sarasota import SarasotaCountyScraper
from scrapers.counties.sarasota_contract import assess_listing

ROSTER = "<html><table><tr><th>Booking #</th></tr></table></html>"
LISTING = (
    '<a href="viewInmate.php?id=1000000001">DOE, JANE Q - 01/01/1990</a>'
)


def _env(**extra):
    base = {RELAY_URL_ENV: "", RELAY_TOKEN_ENV: "", SOLVER_ENV: ""}
    base.update(extra)
    return base


def _boom(*_a, **_k):
    raise ConnectionError("down")


def test_strategy_selection_is_only_the_two_jails():
    manatee = plan_for("Manatee", "FL")
    sarasota = plan_for("Sarasota", "fl")
    assert manatee.order == (MAC_RELAY, STEALTH_SOLVER, FAIL_CLOSED)
    assert sarasota.order == manatee.order
    assert manatee.allow_solver and sarasota.allow_solver
    assert ManateeCountyScraper.ACCESS_PLAN is manatee or ManateeCountyScraper.ACCESS_PLAN == manatee
    assert SarasotaCountyScraper.ACCESS_PLAN == sarasota
    lee = plan_for("Lee")
    assert lee.order == (PLAIN,) and lee.allow_solver is False
    hills = plan_for("Hillsborough", "FL")
    assert hills.order == (PLAIN,) and hills.allow_solver is False


def test_solver_missing_fails_closed_without_calling_the_browser():
    called = {"stealth": 0}

    def stealth(*_a, **_k):
        called["stealth"] += 1
        return 200, ROSTER

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{RELAY_URL_ENV: "http://mac.invalid", RELAY_TOKEN_ENV: "tok"}),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is False
    assert result.strategy == FAIL_CLOSED
    assert result.body == ""
    assert called["stealth"] == 0
    assert [a.reason for a in result.attempts] == ["relay_unreachable", "solver_missing", "all_paths_failed"]


def test_hillsborough_solver_is_never_called_even_if_a_key_is_set():
    called = {"stealth": 0}

    def stealth(*_a, **_k):
        called["stealth"] += 1
        return 200, ROSTER

    plan = plan_for("Hillsborough")
    # Even a hand-built order cannot turn the solver on for this county.
    from scrapers.access_strategy import AccessPlan
    forced = AccessPlan("Hillsborough", "FL", (STEALTH_SOLVER, FAIL_CLOSED), "https://example.invalid", False)
    assert plan.allow_solver is False
    result = run_access(forced, env=_env(**{SOLVER_ENV: "present"}), relay_get=_boom, stealth_fetch=stealth)
    assert called["stealth"] == 0
    assert result.ok is False
    assert result.attempts[0].reason == "solver_not_allowed"


def test_relay_success_does_not_call_the_solver(monkeypatch):
    monkeypatch.delenv(SOLVER_ENV, raising=False)

    def relay_get(url, token, county, state, target):
        assert token == "tok" and county == "Sarasota" and state == "FL"
        assert target.startswith("https://cms.revize.com/")
        return 200, LISTING

    def stealth(*_a, **_k):
        raise AssertionError("solver must not run after a relay hit")

    result = run_access(
        plan_for("Sarasota"),
        env=_env(**{RELAY_URL_ENV: "http://100.64.0.2:8765", RELAY_TOKEN_ENV: "tok"}),
        relay_get=relay_get,
        stealth_fetch=stealth,
    )
    assert result.ok is True
    assert result.strategy == MAC_RELAY
    assert result.reason == "relay_ok"
    assert len(result.attempts) == 1


def test_relay_unreachable_falls_back_to_stealth():
    def stealth(url, key):
        assert key == "solver-key"
        assert "manatee-sheriff" in url
        return 200, ROSTER

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is True
    assert result.strategy == STEALTH_SOLVER
    assert [a.reason for a in result.attempts] == ["relay_unreachable", "stealth_ok"]


def test_relay_blocked_does_not_fall_through_to_the_solver():
    """A relay that reached the site and got a challenge stops. No solver call."""
    def stealth(*_a, **_k):
        raise AssertionError("solver must not run after relay_blocked")

    blocked = run_access(
        plan_for("Manatee"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=lambda *_a, **_k: (403, "Attention Required"),
        stealth_fetch=stealth,
    )
    assert blocked.ok is False
    assert blocked.strategy == FAIL_CLOSED
    assert blocked.reason == "relay_blocked"
    assert blocked.body == ""
    assert [a.reason for a in blocked.attempts] == ["relay_blocked", "relay_blocked"]

    challenged = run_access(
        plan_for("Sarasota"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=lambda *_a, **_k: (200, "<html>Just a moment...</html>"),
        stealth_fetch=stealth,
    )
    assert challenged.reason == "relay_blocked"
    assert [a.strategy for a in challenged.attempts] == [MAC_RELAY, FAIL_CLOSED]


def test_relay_429_and_503_are_blocked_and_do_not_call_the_solver():
    """A reachable relay rate-limit or 503 is a challenge, not a reason to solve."""
    def stealth(*_a, **_k):
        raise AssertionError("solver must not run after a challenge status")

    env = _env(**{
        RELAY_URL_ENV: "http://100.64.0.2:8765",
        RELAY_TOKEN_ENV: "tok",
        SOLVER_ENV: "solver-key",
    })
    for status in (429, 503):
        result = run_access(
            plan_for("Manatee"),
            env=env,
            relay_get=lambda *_a, status=status, **_k: (status, "busy"),
            stealth_fetch=stealth,
        )
        assert result.ok is False
        assert result.reason == "relay_blocked"
        assert result.body == ""
        assert [a.strategy for a in result.attempts] == [MAC_RELAY, FAIL_CLOSED]


def test_any_reachable_relay_error_does_not_call_the_solver():
    """401, 404, 500, and 502 from a live relay stop. Only a dead relay falls through."""
    def stealth(*_a, **_k):
        raise AssertionError("solver must not run after the relay answered")

    env = _env(**{
        RELAY_URL_ENV: "http://100.64.0.2:8765",
        RELAY_TOKEN_ENV: "tok",
        SOLVER_ENV: "solver-key",
    })
    for status in (401, 404, 500, 502):
        result = run_access(
            plan_for("Sarasota"),
            env=env,
            relay_get=lambda *_a, status=status, **_k: (status, "nope"),
            stealth_fetch=stealth,
        )
        assert result.ok is False
        assert result.reason == "relay_rejected"
        assert result.body == ""
        assert [a.strategy for a in result.attempts] == [MAC_RELAY, FAIL_CLOSED]


def test_all_paths_failing_stays_fail_closed_and_emits_nothing():
    def stealth(*_a, **_k):
        return 403, "Just a moment"

    result = run_access(
        plan_for("Manatee"),
        env=_env(**{
            RELAY_URL_ENV: "http://100.64.0.2:8765",
            RELAY_TOKEN_ENV: "tok",
            SOLVER_ENV: "solver-key",
        }),
        relay_get=_boom,
        stealth_fetch=stealth,
    )
    assert result.ok is False
    assert result.strategy == FAIL_CLOSED
    assert result.body == ""
    assert result.reason == "all_paths_failed"
    assert [a.reason for a in result.attempts] == [
        "relay_unreachable", "stealth_blocked", "all_paths_failed",
    ]


def test_missing_relay_config_is_not_a_success_and_scrapers_stay_closed():
    result = run_access(
        plan_for("Sarasota"),
        env=_env(),
        relay_get=_boom,
        stealth_fetch=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no")),
    )
    assert result.attempts[0].reason == "relay_not_configured"
    assert result.ok is False
    assert ManateeCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert SarasotaCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert ManateeCountyScraper().scrape() == []
    assert SarasotaCountyScraper().scrape() == []


def test_sarasota_listing_person_id_is_not_a_booking_number():
    verdict = assess_listing(LISTING)
    assert verdict.reopen_ok is False
    assert "booking number" in " ".join(verdict.reasons)
    assert "1000000001" not in " ".join(verdict.reasons)


def test_mac_relay_allowlist_rejects_other_counties():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "mac_roster_relay.py"
    spec = importlib.util.spec_from_file_location("mac_roster_relay", path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    authorized, read_allowlisted = relay.authorized, relay.read_allowlisted

    assert authorized("tok", "tok") is True
    assert authorized("nope", "tok") is False
    assert authorized("", "tok") is False

    def fetch(url):
        assert url == "https://manatee-sheriff.revize.com/bookings"
        return 200, ROSTER

    assert read_allowlisted("Manatee", "FL", fetch) == (200, ROSTER)
    assert read_allowlisted("Hillsborough", "FL", fetch) == (404, "")
    assert read_allowlisted("Lee", "FL", lambda *_a: (_ for _ in ()).throw(AssertionError("fetched"))) == (404, "")


class _Titles:
    def __init__(self, titles, html, status=200):
        self._titles = list(titles)
        self.html = html
        self.status = status
        self.urls = []
        self.inflight = 0
        self.max_inflight = 0

    def goto(self, url, **kwargs):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        self.urls.append(url)
        self.inflight -= 1
        return type("Resp", (), {"status": self.status})()

    def title(self):
        if len(self._titles) > 1:
            return self._titles.pop(0)
        return self._titles[0]

    def content(self):
        return self.html


def _load_relay():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "mac_roster_relay.py"
    spec = importlib.util.spec_from_file_location("mac_roster_relay_browser", path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    return relay


def test_headed_browser_returns_200_after_the_challenge_clears():
    relay = _load_relay()
    page = _Titles(["Just a moment...", "Manatee Sheriff Search"], "<html>booking table</html>", status=403)
    browser = relay.HeadedBrowser(page_factory=lambda: page, timeout_s=5, sleep=lambda _s: None)
    status, html = browser.fetch("https://manatee-sheriff.revize.com/bookings")
    assert status == 200
    assert html == "<html>booking table</html>"
    status2, html2 = browser.fetch("https://cms.revize.com/revize/apps/sarasota/")
    # The second navigation's title is not a challenge and the fake response
    # status is still 403, so that status is kept.
    assert status2 == 403 and html2 == "<html>booking table</html>"
    assert page.urls == [
        "https://manatee-sheriff.revize.com/bookings",
        "https://cms.revize.com/revize/apps/sarasota/",
    ]
    assert relay.parse_args([]).headless is False
    assert relay.parse_args(["--headless"]).headless is True
    assert "solvecaptcha" not in Path_source(relay)


def Path_source(relay):
    return open(relay.__file__, encoding="utf-8").read().lower()


def test_non_challenge_http_errors_keep_their_status():
    relay = _load_relay()
    for status, title in ((404, "Not Found"), (429, "Too Many Requests"), (500, "Server Error"), (503, "Unavailable")):
        page = _Titles([title], f"<html>{title}</html>", status=status)
        browser = relay.HeadedBrowser(page_factory=lambda p=page: p, timeout_s=1, sleep=lambda _s: None)
        got, html = browser.fetch("https://manatee-sheriff.revize.com/bookings")
        assert got == status, title
        assert title in html


def test_headed_browser_returns_non_200_when_the_challenge_stays():
    relay = _load_relay()
    page = _Titles(["Attention Required! | Cloudflare"], "<html>Attention Required</html>", status=403)
    browser = relay.HeadedBrowser(page_factory=lambda: page, timeout_s=0, sleep=lambda _s: None)
    status, html = browser.fetch("https://manatee-sheriff.revize.com/bookings")
    assert status != 200 and status >= 400
    assert "Attention Required" in html
    assert relay.read_allowlisted("Hillsborough", "FL", browser.fetch) == (404, "")


def test_browser_fetches_do_not_overlap():
    relay = _load_relay()
    import threading

    class _Gate(_Titles):
        def __init__(self):
            super().__init__(["Manatee Sheriff Search"], "<html>ok</html>", status=200)
            self._mu = threading.Lock()

        def goto(self, url, **kwargs):
            with self._mu:
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
            import time as _time
            _time.sleep(0.05)
            with self._mu:
                self.inflight -= 1
            self.urls.append(url)
            return type("Resp", (), {"status": self.status})()

    page = _Gate()
    browser = relay.HeadedBrowser(page_factory=lambda: page, timeout_s=2, sleep=lambda _s: None)
    errors = []

    def one(url):
        try:
            browser.fetch(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(f"https://example.invalid/{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert page.max_inflight == 1
    assert len(page.urls) == 2


def test_closed_browser_is_recreated_on_the_next_request():
    relay = _load_relay()
    made = []

    class _Dead:
        def __init__(self):
            self.closed = False
        def goto(self, url, **kwargs):
            self.closed = True
            raise RuntimeError("Target page, context or browser has been closed")
        def is_closed(self):
            return self.closed

    class _Live:
        def goto(self, url, **kwargs):
            self.url = url
            return type("Resp", (), {"status": 200})()
        def title(self):
            return "Manatee Sheriff Search"
        def content(self):
            return "<html>booking</html>"
        def is_closed(self):
            return False

    def factory():
        page = _Dead() if not made else _Live()
        made.append(page)
        return page

    browser = relay.HeadedBrowser(page_factory=factory, timeout_s=1, sleep=lambda _s: None)
    try:
        browser.fetch("https://manatee-sheriff.revize.com/bookings")
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    status, html = browser.fetch("https://manatee-sheriff.revize.com/bookings")
    assert status == 200
    assert html == "<html>booking</html>"
    assert len(made) == 2


class _RelayConn:
    """Enough of a socket for BaseHTTPRequestHandler. No network."""

    def __init__(self, data: bytes):
        import io
        self.inbound = io.BytesIO(data)
        self.outbound = bytearray()

    def makefile(self, mode="rb", *args, **kwargs):
        if "r" in mode:
            return self.inbound
        import io
        return io.BytesIO()

    def sendall(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.outbound.extend(data)


def _drive_fetch(relay, query: str, token: str, fetch):
    import io
    import json
    from unittest.mock import patch

    raw = (
        f"GET /fetch?{query} HTTP/1.1\r\n"
        f"Host: 127.0.0.1\r\n"
        f"X-Shamrock-Relay-Token: {token}\r\n"
        "X-Trace: header-sentinel\r\n"
        "\r\n"
    ).encode()
    conn = _RelayConn(raw)
    stderr = io.StringIO()
    with patch.object(relay.sys, "stderr", stderr):
        relay.make_handler(token, fetch)(conn, ("127.0.0.1", 0), None)
    head, _, body = bytes(conn.outbound).partition(b"\r\n\r\n")
    status = int(head.split()[1])
    return status, json.loads(body.decode()), stderr.getvalue()


def test_mac_relay_fetch_failed_logs_county_and_redacts_the_token():
    relay = _load_relay()
    token = "relay-token-9f3c2a7b"
    page_body = "<html>page-body-not-logged</html>"

    def fetch(_url):
        raise RuntimeError("nav failed\n" + ("x" * 40) + token + "\r\ny" * 80)

    status, payload, err = _drive_fetch(relay, "county=Manatee&state=FL", token, fetch)
    assert status == 502
    assert payload == {"error": "fetch_failed", "error_type": "RuntimeError"}
    failed = [line for line in err.splitlines() if line.startswith("[mac-relay] fetch_failed ")]
    assert failed == [
        line for line in err.splitlines() if "fetch_failed" in line
    ]
    assert len(failed) == 1
    prefix = "[mac-relay] fetch_failed county=Manatee error_type=RuntimeError detail="
    assert failed[0].startswith(prefix)
    detail = failed[0][len(prefix):]
    assert len(detail) <= 200
    assert token not in err
    assert "[redacted]" in detail
    assert page_body not in err
    assert "GET /fetch" not in err
    assert "X-Shamrock-Relay-Token" not in err
    assert "header-sentinel" not in err
    assert "[mac-relay] request" in err.splitlines()


def test_redaction_marker_does_not_loop_when_the_token_is_inside_redacted():
    relay = _load_relay()
    for token in ("redacted", "act", "["):
        marker = relay._redaction_marker(token)
        assert token not in marker
        cleaned = relay._redact_secret(f"before {token} after {token}", token)
        assert token not in cleaned

    token = "redacted"

    def fetch(_url):
        raise RuntimeError(f"display {token} closed")

    status, payload, err = _drive_fetch(relay, "county=Sarasota&state=FL", token, fetch)
    assert status == 502
    assert payload == {"error": "fetch_failed", "error_type": "RuntimeError"}
    assert "county=Sarasota" in err
    assert "error_type=RuntimeError" in err
    assert token not in err


def test_mac_relay_success_logs_only_the_request_line():
    relay = _load_relay()
    token = "relay-token-success-9f3c"
    page_body = "<html>roster-body-marker</html>"

    def fetch(url):
        assert url == "https://cms.revize.com/revize/apps/sarasota/"
        return 200, page_body

    status, payload, err = _drive_fetch(relay, "county=Sarasota&state=FL", token, fetch)
    assert status == 200
    assert payload == {"status": 200, "body": page_body}
    assert err == "[mac-relay] request\n"
    assert token not in err
    assert page_body not in err
    assert "fetch_failed" not in err
    assert "GET /fetch" not in err


def test_browser_worker_startup_line_redacts_the_token(monkeypatch):
    import io
    from unittest.mock import patch

    relay = _load_relay()
    token = "relay-token-worker-44aa"
    monkeypatch.setenv("SHAMROCK_MAC_RELAY_TOKEN", token)
    started = io.StringIO()
    with patch.object(relay.sys, "stderr", started):
        relay.HeadedBrowser(page_factory=lambda: object(), timeout_s=0, sleep=lambda _s: None)
    assert started.getvalue() == "[mac-relay] browser_worker started\n"
    assert token not in started.getvalue()

    def boom(self):
        raise RuntimeError(f"display missing {token}\nsecond line")

    monkeypatch.setattr(relay.threading.Thread, "start", boom)
    failed = io.StringIO()
    with patch.object(relay.sys, "stderr", failed):
        try:
            relay.HeadedBrowser(page_factory=lambda: object(), timeout_s=0, sleep=lambda _s: None)
            raised = False
        except RuntimeError:
            raised = True
    assert raised
    text = failed.getvalue()
    assert text.startswith("[mac-relay] browser_worker failed error_type=RuntimeError detail=")
    assert text.count("\n") == 1
    assert token not in text
    assert "[redacted]" in text
    assert "second line" in text
