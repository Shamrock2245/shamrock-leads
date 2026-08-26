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
        "/api/paperwork/shannon/email",
    ):
        assert any(path.startswith(p) for p in OPEN_PREFIXES), path


def test_imessage_status_is_public_health_path():
    from dashboard.auth.pin_middleware import OPEN_PATHS

    assert "/api/imessage/status" in OPEN_PATHS


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
