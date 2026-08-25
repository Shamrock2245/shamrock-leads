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


def test_shannon_voice_rejects_unsigned_placeholder_email():
    with pytest.raises(DocuSealPacketValidationError):
        validate_shannon_voice_packet(
            packet_id="SH-1",
            bond_data={"surety_id": "osi", "defendant_name": "Jane Doe"},
            indemnitors=[{"name": "John Cosigner", "email": "unsigned+x@example.com"}],
            defendant={"name": "Jane Doe"},
        )
