"""Production uptime watchdog: health judgment and prod-down issue dedupe."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.prod_uptime_watchdog import (
    CHECKS,
    Action,
    OpenIssue,
    judge_body,
    plan_action,
    probe_check,
    render_down_body,
    signature_for,
)


def test_public_checks_are_only_the_open_health_routes():
    urls = {url for _name, url in CHECKS}
    assert urls == {
        "https://leads.shamrockbailbonds.biz/health",
        "https://leads.shamrockbailbonds.biz/health/live",
        "https://paperwork.shamrockbailbonds.biz/health",
        "https://paperwork.shamrockbailbonds.biz/health/live",
    }
    assert all(url.endswith("/health") or url.endswith("/health/live") for url in urls)


def test_judge_body_accepts_ok_and_rejects_degraded_or_html():
    ok, detail = judge_body(200, '{"status":"ok","engine":"fastapi","database":"connected"}', None)
    assert ok is True
    assert "status=ok" in detail
    assert "database=connected" in detail

    bad, detail = judge_body(503, '{"status":"degraded","database":"unreachable"}', None)
    assert bad is False
    assert "503" in detail
    assert "unreachable" not in detail or "HTTP 503" in detail

    bad, detail = judge_body(200, "<html>login</html>", None)
    assert bad is False
    assert "not JSON" in detail

    bad, detail = judge_body(None, "", "URLError")
    assert bad is False
    assert detail == "URLError"


def test_probe_retries_then_succeeds_without_sleeping_on_success():
    calls = {"n": 0}

    def fetch(_url):
        calls["n"] += 1
        if calls["n"] < 2:
            return None, "", "URLError"
        return 200, '{"status":"ok","engine":"fastapi"}', None

    slept = []
    result = probe_check("leads /health", CHECKS[0][1], fetch=fetch, sleep=slept.append, attempts=3)
    assert result.ok is True
    assert calls["n"] == 2
    assert slept == [4]


def test_issue_plan_dedupes_and_closes_on_recovery():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    fresh = OpenIssue(number=12, last_signature="leads /health:HTTP 503", last_marker_at=now - timedelta(minutes=10))
    stale = OpenIssue(number=12, last_signature="leads /health:HTTP 503", last_marker_at=now - timedelta(hours=2))

    assert plan_action(True, "ok", None, now) is Action.NONE
    assert plan_action(False, "leads /health:HTTP 503", None, now) is Action.OPEN
    assert plan_action(False, "leads /health:HTTP 503", fresh, now) is Action.SKIP_COMMENT
    assert plan_action(False, "leads /health/live:HTTP 503", fresh, now) is Action.COMMENT
    assert plan_action(False, "leads /health:HTTP 503", stale, now) is Action.COMMENT
    assert plan_action(True, "ok", fresh, now) is Action.CLOSE


def test_down_body_has_a_stable_marker_and_no_raw_payload():
    from scripts.prod_uptime_watchdog import ProbeResult

    results = [
        ProbeResult("leads /health", CHECKS[0][1], False, "HTTP 503"),
        ProbeResult("leads /health/live", CHECKS[1][1], True, "status=ok"),
    ]
    body = render_down_body(results, "https://github.com/Shamrock2245/shamrock-leads/actions/runs/1")
    assert "<!-- shamrock-prod-uptime signature=" in body
    assert signature_for(results) in body
    assert "prod-down" not in body or "Disable" in body
    assert "<html" not in body
