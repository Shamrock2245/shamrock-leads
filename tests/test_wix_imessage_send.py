"""Wix clipboard iMessage goes through Super CRM machine auth. Never Twilio. Never a live send in these tests."""
from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse


def test_wix_machine_route_bypasses_pin_middleware():
    from dashboard.auth.pin_middleware import OPEN_PREFIXES

    assert any("/api/imessage/wix/send".startswith(p) for p in OPEN_PREFIXES)


def test_wix_clipboard_text_uses_bluebubbles_never_twilio(monkeypatch):
    from dashboard.services import bb_client

    captured = {}

    async def fake_universal(phone, message, method="private-api"):
        captured["phone"] = phone
        captured["message"] = message
        captured["method"] = method
        return {"success": True, "sent": True, "queued": False, "channel": "imessage"}

    monkeypatch.setattr(bb_client, "send_message_universal", fake_universal)
    result = asyncio.run(bb_client.send_wix_clipboard_text("+12395550178", "Signature complete"))
    assert result["rail"] == "bluebubbles"
    assert result["source"] == "wix_clipboard"
    assert result["success"] is True
    assert captured["phone"] == "+12395550178"
    assert "twilio" not in str(result).lower()


def test_wix_send_missing_fields_does_not_dispatch(monkeypatch):
    from dashboard.routers import imessage_automation

    called = []

    monkeypatch.setattr(imessage_automation, "_require_control_auth", lambda *a, **k: None)

    async def boom(*_a, **_k):
        called.append(True)
        return {"success": True, "sent": True}

    monkeypatch.setattr(imessage_automation, "send_wix_clipboard_text", boom)

    class FakeRequest:
        async def json(self):
            return {}

    result = asyncio.run(imessage_automation.wix_clipboard_send_text(FakeRequest()))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    assert called == []


def test_wix_send_requires_machine_or_staff_auth(monkeypatch):
    from dashboard.routers import imessage_automation

    called = []
    monkeypatch.setattr(
        imessage_automation,
        "_require_control_auth",
        lambda *a, **k: JSONResponse({"success": False, "error": "Authentication required"}, status_code=401),
    )

    async def boom(*_a, **_k):
        called.append(True)
        return {"success": True}

    monkeypatch.setattr(imessage_automation, "send_wix_clipboard_text", boom)

    class FakeRequest:
        async def json(self):
            return {"phone": "+12395550100", "message": "do not send"}

    result = asyncio.run(imessage_automation.wix_clipboard_send_text(FakeRequest()))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 401
    assert called == []
