"""Shannon 24/7 voice paperwork email path."""
from __future__ import annotations

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


def test_shannon_voice_rejects_unsigned_placeholder_email():
    with pytest.raises(DocuSealPacketValidationError):
        validate_shannon_voice_packet(
            packet_id="SH-1",
            bond_data={"surety_id": "osi", "defendant_name": "Jane Doe"},
            indemnitors=[{"name": "John Cosigner", "email": "unsigned+x@example.com"}],
            defendant={"name": "Jane Doe"},
        )
