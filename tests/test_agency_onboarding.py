"""Agency onboarding. No mail, no Stripe, no production database."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.platform_onboarding import router
from dashboard.services import agency_onboarding as onboarding
from tests.test_tenant_scope import MemoryCollection

ROOT = Path(__file__).resolve().parents[1]
_SERVICE = Path(onboarding.__file__).read_text(encoding="utf-8")


def _payload(**overrides):
    body = {
        "legal_name": "Gulf Coast Bail",
        "home_state": "FL",
        "owner": {
            "name": "Ada Owner",
            "email": "ada@gulf.example",
            "phone": "239-555-0100",
        },
        "licenses": [
            {"number": "W123456", "state": "FL", "name_on_license": "Ada Owner"}
        ],
        "branding": {"display_name": "Gulf Coast", "primary_color": "#0b6b3a"},
        "invites": [{"email": "clerk@gulf.example", "role": "staff"}],
        "integrations": {
            "texting": {"provider": "twilio", "secret_ref": "env:GULF_TWILIO_TOKEN"},
            "payments": {"provider": "none"},
        },
    }
    body.update(overrides)
    return body


def _install_db(monkeypatch):
    tenants = MemoryCollection()
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr("dashboard.extensions._mongo_db", {"tenants": tenants})
    return tenants


def _client(monkeypatch, *, admin=False, flag=True):
    if flag:
        monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    else:
        monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
        monkeypatch.setattr(
            "dashboard.extensions.get_mongo_client",
            lambda: (_ for _ in ()).throw(AssertionError("mongo touched while flag is off")),
        )
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request, call_next):
        if admin:
            request.state.sl_email = "admin@shamrockbailbonds.biz"
            request.state.sl_role = "god_admin"
            request.state.sl_tenant_id = "shamrock"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _tokens(doc):
    return [row.get("token") for row in doc.get("invites") or []]


def test_service_does_not_import_a_mailer_or_stripe():
    for banned in ("smtplib", "import requests", "import stripe", "import httpx", "import twilio", "from twilio"):
        assert banned not in _SERVICE
    assert onboarding.__dict__.get("smtplib") is None


def test_flag_off_routes_404_and_do_not_write(monkeypatch):
    client = _client(monkeypatch, admin=True, flag=False)
    assert client.get("/platform").status_code == 404
    assert client.get("/signup").status_code == 404
    assert client.post("/api/platform/tenants", json=_payload()).status_code == 404
    assert client.post("/api/public/signup", json=_payload()).status_code == 404
    assert client.post("/api/platform/tenants/gulf/approve").status_code == 404


def test_signup_page_lists_the_five_steps(monkeypatch):
    _install_db(monkeypatch)
    client = _client(monkeypatch, admin=False, flag=True)
    page = client.get("/signup")
    assert page.status_code == 200
    assert 'data-mode="signup"' in page.text
    for label in ("Agency", "Licenses", "Branding", "Staff", "Connections"):
        assert label in page.text
    assert "are not sent" in page.text or "do not email" in page.text
    admin = _client(monkeypatch, admin=True, flag=True)
    home = admin.get("/platform")
    assert home.status_code == 200
    assert 'data-mode="admin"' in home.text
    stranger = _client(monkeypatch, admin=False, flag=True)
    denied = stranger.get("/platform")
    assert denied.status_code == 403
    assert denied.json()["error"] == "platform_admin_required"


def test_super_admin_can_create_pending_or_active(monkeypatch):
    tenants = _install_db(monkeypatch)
    client = _client(monkeypatch, admin=True, flag=True)
    pending = client.post("/api/platform/tenants", json=_payload())
    assert pending.status_code == 201
    body = pending.json()
    assert body["tenant_id"] == "gulf_coast_bail"
    assert body["status"] == "pending_approval"
    assert body["invites_sent"] == 0
    assert body["license_count"] == 1
    assert all("token" not in row for row in body["invites"])
    stored = tenants.docs[0]
    assert stored["tenant_id"] == "gulf_coast_bail"
    assert stored["invites_sent"] == 0
    assert _tokens(stored) and all(_tokens(stored))
    assert stored["invites"][0]["token"] not in pending.text

    active = client.post(
        "/api/platform/tenants",
        json=_payload(legal_name="Palmetto Desk", approve_now=True),
    )
    assert active.status_code == 201
    assert active.json()["status"] == "active"
    assert active.json()["tenant_id"] == "palmetto_desk"


def test_self_serve_cannot_approve_and_hides_invite_tokens(monkeypatch):
    tenants = _install_db(monkeypatch)
    client = _client(monkeypatch, admin=False, flag=True)
    created = client.post("/api/public/signup", json=_payload(approve_now=True))
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending_approval"
    assert body["invites_sent"] == 0
    assert "invites" not in body
    stored_tokens = _tokens(tenants.docs[0])
    assert stored_tokens and stored_tokens[0] not in created.text
    dumped = json.dumps(body)
    assert "token" not in dumped
    assert "env:GULF_TWILIO_TOKEN" in dumped


def test_secrets_and_reserved_slug_are_rejected(monkeypatch):
    tenants = _install_db(monkeypatch)
    client = _client(monkeypatch, admin=True, flag=True)
    leaked = client.post(
        "/api/platform/tenants",
        json=_payload(integrations={"payments": {"provider": "swipesimple", "api_key": "sk_live_nope"}}),
    )
    assert leaked.status_code == 400
    assert leaked.json()["error"] == "secret_ref_only"
    assert tenants.docs == []
    reserved = client.post("/api/public/signup", json=_payload(legal_name="shamrock", slug="shamrock"))
    assert reserved.status_code == 400
    assert reserved.json()["error"] == "slug_invalid"
    assert tenants.docs == []


def test_approve_and_reject(monkeypatch):
    _install_db(monkeypatch)
    client = _client(monkeypatch, admin=True, flag=True)
    assert client.post("/api/public/signup", json=_payload()).status_code == 201
    missing_reason = client.post("/api/platform/tenants/gulf_coast_bail/reject", json={})
    assert missing_reason.status_code == 400
    assert missing_reason.json()["error"] == "reason_required"
    rejected = client.post(
        "/api/platform/tenants/gulf_coast_bail/reject",
        json={"reason": "License number does not match the owner name"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    again = client.post("/api/platform/tenants/gulf_coast_bail/approve")
    assert again.status_code == 400
    assert again.json()["error"] == "not_pending"

    assert client.post("/api/public/signup", json=_payload(legal_name="Second Desk")).status_code == 201
    approved = client.post("/api/platform/tenants/second_desk/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"
    shamrock = client.post("/api/platform/tenants/shamrock/approve")
    assert shamrock.status_code == 400
    assert shamrock.json()["error"] == "slug_invalid"
    missing = client.post("/api/platform/tenants/nobody_agency/approve")
    assert missing.status_code == 404


def test_non_admin_cannot_open_the_console_api(monkeypatch):
    tenants = _install_db(monkeypatch)
    client = _client(monkeypatch, admin=False, flag=True)
    listing = client.get("/api/platform/tenants")
    assert listing.status_code == 403
    created = client.post("/api/platform/tenants", json=_payload())
    assert created.status_code == 403
    assert tenants.docs == []


def test_signup_bypasses_pin_and_platform_stays_gated(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PIN", "9999")
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    from dashboard.auth.pin_middleware import PinAuthMiddleware

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(PinAuthMiddleware)
    client = TestClient(app)
    signup = client.get("/signup")
    assert signup.status_code == 404
    public = client.post("/api/public/signup", json=_payload())
    assert public.status_code == 404
    platform = client.get("/platform", follow_redirects=False)
    assert platform.status_code == 302
    assert "/login" in platform.headers["location"]


def test_duplicate_slug_is_rejected(monkeypatch):
    _install_db(monkeypatch)
    client = _client(monkeypatch, admin=True, flag=True)
    assert client.post("/api/platform/tenants", json=_payload()).status_code == 201
    again = client.post("/api/platform/tenants", json=_payload())
    assert again.status_code == 400
    assert again.json()["error"] == "slug_taken"
