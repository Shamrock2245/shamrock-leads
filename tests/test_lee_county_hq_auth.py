"""Lee County HQ API (/api/lee-county/*) is staff-only.

Every route on ``lee_county_hq_bp`` must answer 401 with no PIN session and
still work with one. Sends, sweeps, and Mongo are mocked; nothing real runs.
Synthetic values only.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, OPEN_PATHS, OPEN_PREFIXES, _sign_token
from dashboard.main import app
from dashboard.routers.lee_county_hq import lee_county_hq_bp

client = TestClient(app)

HQ = "dashboard.routers.lee_county_hq"

# (method, concrete path, json body) for every route on the router.
CASES = {
    ("GET", "/api/lee-county/overview"): ("/api/lee-county/overview", None),
    ("GET", "/api/lee-county/leads"): ("/api/lee-county/leads?limit=5", None),
    ("GET", "/api/lee-county/defendant/{booking_number}/family"): (
        "/api/lee-county/defendant/B-TEST-1/family", None),
    ("POST", "/api/lee-county/outreach/send"): (
        "/api/lee-county/outreach/send",
        {"booking_number": "B-TEST-1", "recipient_phone": "+15550100100", "recipient_name": "Test Contact"},
    ),
    ("GET", "/api/lee-county/autopilot/config"): ("/api/lee-county/autopilot/config", None),
    ("POST", "/api/lee-county/autopilot/config"): ("/api/lee-county/autopilot/config", {"enabled": False}),
    ("POST", "/api/lee-county/autopilot/sweep"): ("/api/lee-county/autopilot/sweep", None),
    ("GET", "/api/lee-county/calculator"): ("/api/lee-county/calculator?bond=1000", None),
}


def _router_routes():
    out = set()
    for route in lee_county_hq_bp.routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.add((method, route.path))
    return out


@pytest.fixture(autouse=True)
def _prod_like_auth_env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PIN", "synthetic-test-pin-0000")
    monkeypatch.setenv("SECRET_KEY", "synthetic-test-secret-key-not-real")
    for var in ("GAS_API_KEY", "LEADS_INTERNAL_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mocked_backend():
    """Mock every DB and side-effect call the router makes."""
    col = MagicMock()
    col.find_one = AsyncMock(return_value=None)
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mocks = {
        "get_db": MagicMock(return_value=MagicMock()),
        "get_collection": MagicMock(return_value=col),
        "get_lee_county_overview": AsyncMock(return_value={"total": 0}),
        "get_lee_county_leads": AsyncMock(return_value={"leads": [], "total": 0}),
        "find_family_contacts": AsyncMock(return_value=[]),
        "send_lee_county_outreach": AsyncMock(return_value={"ok": True, "sent": False}),
        "run_lee_county_autopilot_sweep": AsyncMock(return_value={"ok": True, "swept": 0}),
        "generate_family_outreach_message": MagicMock(return_value="synthetic message"),
    }
    patches = [patch(f"{HQ}.{name}", mock) for name, mock in mocks.items()]
    for p in patches:
        p.start()
    try:
        yield mocks
    finally:
        for p in patches:
            p.stop()


def _call(method, path, body, cookies=None):
    if cookies:
        client.cookies.set(COOKIE_NAME, cookies)
    try:
        if method == "GET":
            return client.get(path)
        return client.post(path, json=body if body is not None else {})
    finally:
        client.cookies.clear()


def test_every_router_route_is_covered():
    assert _router_routes() == set(CASES)


@pytest.mark.parametrize("key", sorted(CASES), ids=lambda k: f"{k[0]} {k[1]}")
def test_lee_county_route_requires_auth(key, mocked_backend):
    method, _ = key
    path, body = CASES[key]
    resp = _call(method, path, body)
    assert resp.status_code == 401
    assert resp.json() == {"error": "Authentication required"}
    for name in ("send_lee_county_outreach", "run_lee_county_autopilot_sweep", "get_db", "get_collection"):
        assert not mocked_backend[name].called, name


@pytest.mark.parametrize("key", sorted(CASES), ids=lambda k: f"{k[0]} {k[1]}")
def test_lee_county_route_works_with_staff_session(key, mocked_backend):
    method, _ = key
    path, body = CASES[key]
    token = _sign_token(role="god_admin", is_admin=True)
    resp = _call(method, path, body, cookies=token)
    assert resp.status_code == 200, resp.text


def test_outreach_send_with_session_uses_the_mocked_sender_only(mocked_backend):
    token = _sign_token(role="god_admin", is_admin=True)
    path, body = CASES[("POST", "/api/lee-county/outreach/send")]
    resp = _call("POST", path, body, cookies=token)
    assert resp.status_code == 200
    assert mocked_backend["send_lee_county_outreach"].await_count == 1


def test_bad_session_cookie_is_rejected(mocked_backend):
    resp = _call("GET", "/api/lee-county/overview", None, cookies="not-a-signed-token")
    assert resp.status_code == 401
    assert not mocked_backend["get_lee_county_overview"].called


def test_calculator_is_not_public(mocked_backend):
    resp = client.get("/api/lee-county/calculator?bond=0")
    assert resp.status_code == 401


def test_allowlist_cannot_reopen_lee_county_prefix():
    """No open path or prefix may cover any Lee County HQ route."""
    assert "/api/lee-county/" not in OPEN_PREFIXES
    assert "/api/lee-county" not in OPEN_PREFIXES
    probes = ["/api/lee-county", "/api/lee-county/"] + [CASES[k][0].split("?")[0] for k in CASES]
    for probe in probes:
        assert probe not in OPEN_PATHS, probe
        for prefix in OPEN_PREFIXES:
            assert not probe.startswith(prefix), (probe, prefix)
