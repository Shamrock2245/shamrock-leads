"""Shannon 24/7 voice paperwork email path."""
from __future__ import annotations

import asyncio

import pytest

from dashboard.services.docuseal_service import (
    DocuSealPacketValidationError,
    validate_shannon_voice_packet,
)


def test_shannon_voice_requires_indemnitor_email():
    with pytest.raises(DocuSealPacketValidationError):
        validate_shannon_voice_packet(
            packet_id="SH-1",
            bond_data={"surety_id": "osi", "defendant_name": "Jane Doe"},
            indemnitors=[{"name": "John Cosigner", "email": ""}],
            defendant={"name": "Jane Doe"},
        )


def test_shannon_voice_allows_packet_without_match_or_poa():
    validate_shannon_voice_packet(
        packet_id="SH-1",
        bond_data={
            "surety_id": "osi",
            "defendant_name": "Jane Doe",
            "indemnitor_email": "ind@example.com",
        },
        indemnitors=[{"name": "John Cosigner", "email": "ind@example.com"}],
        defendant={"name": "Jane Doe"},
    )


def test_shannon_voice_text_uses_bluebubbles_never_twilio(monkeypatch):
    from dashboard.services import bb_client

    captured = {}

    async def fake_universal(phone, message, method="private-api"):
        captured["phone"] = phone
        captured["message"] = message
        captured["method"] = method
        return {"success": True, "sent": True, "queued": False, "channel": "imessage"}

    monkeypatch.setattr(bb_client, "send_message_universal", fake_universal)
    result = asyncio.run(bb_client.send_shannon_voice_text("+12395550178", "Sign and pay"))
    assert result["rail"] == "bluebubbles"
    assert result["success"] is True
    assert captured["phone"] == "+12395550178"
    assert "twilio" not in str(result).lower()


def test_shannon_machine_routes_bypass_pin_middleware():
    from dashboard.auth.pin_middleware import OPEN_PREFIXES

    for path in (
        "/api/agent-brain/memory/lookup",
        "/api/agent-brain/memory/status",
        "/api/imessage/shannon/send",
        "/api/imessage/wix/send",
        "/api/paperwork/shannon/email",
        "/api/paperwork/shannon/id-link",
        "/api/paperwork/shannon/id-status",
        "/paperwork/shannon/id/abc",
    ):
        assert any(path.startswith(p) for p in OPEN_PREFIXES), path


def test_imessage_status_is_public_health_path():
    from dashboard.auth.pin_middleware import OPEN_PATHS

    assert "/api/imessage/status" in OPEN_PATHS
    assert "/api/ops/shannon-health" in OPEN_PATHS


def test_mem0_lookup_returns_returning_client(monkeypatch):
    from dashboard.routers import agent_brain_api

    async def fake_search(phone, query, limit=6):
        assert "2395550100" in "".join(ch for ch in phone if ch.isdigit()) or phone.endswith("0100")
        return ["Caller called Shamrock. Regarding: Jane Doe. Paperwork was sent."]

    monkeypatch.setattr("dashboard.services.mem0_service.search_facts", fake_search)
    monkeypatch.setattr(
        "dashboard.routers.automation_control._require_control_auth",
        lambda *a, **k: None,
    )

    class FakeRequest:
        async def json(self):
            return {"phone": "+12395550100", "query": "prior bail"}

    result = asyncio.run(agent_brain_api.api_agent_brain_memory_lookup(FakeRequest()))
    assert result["returning_client"] == "yes"
    assert "Jane Doe" in (result.get("known_defendant") or "") or "Jane Doe" in result["prior_notes"]


def test_shannon_voice_rejects_unsigned_placeholder_email():
    with pytest.raises(DocuSealPacketValidationError):
        validate_shannon_voice_packet(
            packet_id="SH-1",
            bond_data={"surety_id": "osi", "defendant_name": "Jane Doe"},
            indemnitors=[{"name": "John Cosigner", "email": "unsigned+x@example.com"}],
            defendant={"name": "Jane Doe"},
        )


def test_shannon_voice_rejects_jail_email():
    with pytest.raises(DocuSealPacketValidationError, match="jail"):
        validate_shannon_voice_packet(
            packet_id="SH-1",
            bond_data={"surety_id": "osi", "defendant_name": "Jane Doe"},
            indemnitors=[{"name": "John Cosigner", "email": "booking@leecountyjail.com"}],
            defendant={"name": "Jane Doe"},
        )


def test_shannon_voice_text_blocks_727(monkeypatch):
    from dashboard.services import bb_client

    called = []

    async def boom(*_a, **_k):
        called.append(True)
        return {"success": True, "sent": True}

    monkeypatch.setattr(bb_client, "send_message_universal", boom)
    result = asyncio.run(bb_client.send_shannon_voice_text("+17272952245", "do not send"))
    assert result["success"] is False
    assert result["error"] == "shannon_public_line_blocked"
    assert called == []


def test_rewrite_shannon_packet_id_drops_callsid():
    from dashboard.routers.paperwork import rewrite_shannon_packet_id

    sid = "CAd28b4dd55bba44c17003ee3d18521392"
    out = rewrite_shannon_packet_id(sid)
    assert out.startswith("SH-")
    assert not out.startswith("CA")
    assert rewrite_shannon_packet_id("SH-2395550100-SMITH") == "SH-2395550100-SMITH"


def test_bb_client_recipient_does_not_use_last4_from_line(monkeypatch):
    from dashboard import extensions as ext
    from dashboard.services import bb_client

    ext.BB_SERVERS.clear()
    ext.BB_SERVERS["2399550178"] = {"url": "http://0178.example", "password": "a"}
    ext.BB_SERVERS["2399550314"] = {"url": "http://0314.example", "password": "b"}

    class FakeClient:
        def __init__(self, url, password):
            self.url = url
            self.password = password

    monkeypatch.setattr("dashboard.routers.bb_private_api.BlueBubblesClient", FakeClient)
    client = bb_client.get_bb_client("+12397849365")
    assert client.url == "http://0178.example"
    client_0314 = bb_client.get_bb_client("+12399550314")
    assert client_0314.url == "http://0314.example"
