"""Phase 1: BlueBubbles webhook management endpoints require staff/machine auth.

The inbound receiver (POST /api/webhooks/bluebubbles) must stay open because
BlueBubbles Server sends no HMAC/credential by default; boot-time
registration calls ensure_webhook() directly (no HTTP hop).
No network: BlueBubbles client and Mongo are mocked.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import WEBHOOK_MANAGEMENT_PATHS
from dashboard.main import app
from tests.bb_fake_mongo import FakeDB

client = TestClient(app)

_ENV = {
    "DASHBOARD_PIN": "test-pin-not-real",
    "GAS_API_KEY": "test-gas-key-not-real",
    "ENV": "test",
}
_KEY = {"X-API-Key": "test-gas-key-not-real"}
_SERVERS = {"2399550178": {"url": "http://bb.invalid", "password": "x", "label": "Test 0178"}}


def _fake_bb_client_cls():
    inst = MagicMock()
    inst.ensure_webhook = AsyncMock(return_value={"success": True, "already_existed": True, "data": {}})
    inst.delete_webhook = AsyncMock(return_value={"success": True})
    return MagicMock(return_value=inst), inst


def test_register_path_is_not_webhook_exempt():
    assert "/api/webhooks/bluebubbles/register" in WEBHOOK_MANAGEMENT_PATHS


def test_register_unauthenticated_is_rejected_by_middleware():
    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, _ENV), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        resp = client.post("/api/webhooks/bluebubbles/register", json={"vps_url": "https://evil.invalid"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "Authentication required"}  # PIN middleware, not the route
    inst.ensure_webhook.assert_not_called()


def test_register_unauthenticated_rejected_by_route_even_without_pin():
    """Defense in depth: route-level guard holds even if the middleware is open (dev, no PIN)."""
    env = {k: v for k, v in _ENV.items() if k != "DASHBOARD_PIN"}
    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, env), \
         patch.dict(os.environ, {"DASHBOARD_PIN": ""}), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        resp = client.post("/api/webhooks/bluebubbles/register", json={"vps_url": "https://evil.invalid"})
    assert resp.status_code == 401
    inst.ensure_webhook.assert_not_called()


def test_register_with_machine_key_succeeds():
    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, _ENV), \
         patch.dict("dashboard.routers.bb_webhook_receiver.BB_SERVERS", _SERVERS, clear=True), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        resp = client.post(
            "/api/webhooks/bluebubbles/register",
            json={"vps_url": "https://leads.example.invalid"},
            headers=_KEY,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    inst.ensure_webhook.assert_awaited_once()
    url, events = inst.ensure_webhook.call_args.args
    assert url == "https://leads.example.invalid/api/webhooks/bluebubbles"
    assert "new-message" in events


def test_register_with_staff_session_succeeds():
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, {**_ENV, "SECRET_KEY": "ci-not-a-real-secret"}), \
         patch.dict("dashboard.routers.bb_webhook_receiver.BB_SERVERS", _SERVERS, clear=True), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        token = _sign_token(role="god_admin", is_admin=True)
        resp = client.post(
            "/api/webhooks/bluebubbles/register",
            json={"vps_url": "https://leads.example.invalid"},
            cookies={COOKIE_NAME: token},
        )
    assert resp.status_code == 200
    inst.ensure_webhook.assert_awaited_once()


def test_delete_requires_auth():
    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, _ENV), \
         patch.dict("dashboard.routers.bb_webhook_receiver.BB_SERVERS", _SERVERS, clear=True), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        denied = client.delete("/api/webhooks/bluebubbles/7")
        ok = client.delete("/api/webhooks/bluebubbles/7", headers=_KEY)
    assert denied.status_code == 401
    assert ok.status_code == 200
    inst.delete_webhook.assert_awaited_once_with(7)


def test_delete_route_guard_without_pin():
    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, {**_ENV, "DASHBOARD_PIN": ""}), \
         patch("dashboard.routers.bb_webhook_receiver.BlueBubblesClient", cls):
        resp = client.delete("/api/webhooks/bluebubbles/7")
    assert resp.status_code == 401
    inst.delete_webhook.assert_not_called()


def test_inbound_receiver_still_accepts_unauthenticated_bb_delivery():
    """BB sends no credential: typed events and real new-message must still be 200."""
    db = FakeDB()
    with patch.dict(os.environ, _ENV), \
         patch("dashboard.extensions.get_db", return_value=db), \
         patch("dashboard.extensions.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_bb_server", return_value=None):
        typed = client.post("/api/webhooks/bluebubbles", json={"type": "typing-indicator", "data": {}})
        msg = client.post("/api/webhooks/bluebubbles", json={
            "type": "new-message",
            "data": {
                "guid": "IN-UNAUTH-1",
                "text": "hello from an unknown number",
                "isFromMe": False,
                "handle": {"address": "+12395550199"},
                "chats": [{"guid": "any;-;+12395550199"}],
                "dateCreated": 1790000000000,
            },
        })
    assert typed.status_code == 200
    assert msg.status_code == 200
    assert msg.json()["result"] == {"processed": True, "matched": False}
    rows = db["imessage_outreach"].docs
    assert len(rows) == 1 and rows[0]["status"] == "unmatched"


def test_boot_time_ensure_webhook_is_direct_and_unauthenticated():
    """cron registers via BlueBubblesClient.ensure_webhook — no HTTP call to /register."""
    from dashboard import cron

    cls, inst = _fake_bb_client_cls()
    with patch.dict(os.environ, {"BB_WEBHOOK_PUBLIC_URL": "https://leads.example.invalid/"}), \
         patch.dict("dashboard.extensions.BB_SERVERS", _SERVERS, clear=True), \
         patch("dashboard.routers.bb_private_api.BlueBubblesClient", cls):
        asyncio.run(cron._register_bb_webhooks_background())
    inst.ensure_webhook.assert_awaited_once()
    assert inst.ensure_webhook.call_args.args[0] == "https://leads.example.invalid/api/webhooks/bluebubbles"
