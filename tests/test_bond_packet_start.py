"""Write Bond packet start is a service, and its gates fail closed."""
from __future__ import annotations

import pytest

from dashboard.services.bond_packet_start import start_indemnitor_bond_packet
from dashboard.services.docuseal_service import DocuSealPacketValidationError


@pytest.fixture(autouse=True)
def _assume_verified_indemnitor(monkeypatch):
    """Binding, POA, and template gates. Identity is tested on its own."""

    async def _ok(*_args, **_kwargs):
        return {"ok": True, "exempt": "test_fixture", "issues": [], "methods": ["scan"]}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )


class _FakeDocuSeal:
    def __init__(self):
        self.calls = []
        self.is_configured = True

    async def create_submission_for_packet(self, **kwargs):
        self.calls.append(kwargs)
        return {"submission_id": 9, "submitters": []}


def _bound(**overrides):
    data = {
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "B1",
        "match_status": "validated",
        "surety_id": "osi",
        "bond_amount": 5000,
        "indemnitor": {"name": "Alex Rivera", "email": "a@example.com"},
        "defendant": {"name": "Jordan Lee", "email": "d@example.com"},
        "indemnitors": [{"name": "Alex Rivera", "email": "a@example.com"}],
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_service_refuses_unbound_packet_before_docuseal():
    fake = _FakeDocuSeal()
    with pytest.raises(DocuSealPacketValidationError) as raised:
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            bond_data={"surety_id": "osi", "defendant_name": "Jordan Lee"},
            docuseal=fake,
        )
    assert "missing required binding" in str(raised.value)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_service_refuses_placeholder_and_unvalidated_match():
    fake = _FakeDocuSeal()
    with pytest.raises(DocuSealPacketValidationError):
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            bond_data=_bound(defendant={"name": "To Be Named", "email": "d@example.com"}),
            docuseal=fake,
        )
    with pytest.raises(DocuSealPacketValidationError):
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            bond_data=_bound(match_status="pending"),
            docuseal=fake,
        )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_field_overrides_cannot_satisfy_the_binding_gate():
    fake = _FakeDocuSeal()
    with pytest.raises(DocuSealPacketValidationError):
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            ctx={},
            field_overrides={
                "poa_number": "SHOULD-NOT-BIND",
                "bond_case_id": "SHOULD-NOT-BIND",
                "match_status": "validated",
                "case_number": "26CF1",
            },
            docuseal=fake,
        )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_service_refuses_missing_poa_and_unentitled_tenant(monkeypatch):
    fake = _FakeDocuSeal()
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    with pytest.raises(DocuSealPacketValidationError) as poa_raised:
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            bond_data=_bound(),
            poa_record=None,
            docuseal=fake,
        )
    assert poa_raised.value.code == "docuseal_poa_not_assigned"

    with pytest.raises(DocuSealPacketValidationError) as tier_raised:
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            bond_data=_bound(bond_amount=50000),
            poa_record={"max_bond_value": 5000},
            docuseal=fake,
        )
    assert tier_raised.value.code == "docuseal_poa_tier_invalid"

    with pytest.raises(DocuSealPacketValidationError) as tenant_raised:
        await start_indemnitor_bond_packet(
            packet_id="pkt-1",
            surety_id="osi",
            tenant_id="otheragency",
            bond_data=_bound(),
            poa_record={"max_bond_value": 25000},
            docuseal=fake,
        )
    assert tenant_raised.value.code == "template_unavailable"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_service_submits_only_after_gates_pass(monkeypatch):
    fake = _FakeDocuSeal()
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    result = await start_indemnitor_bond_packet(
        packet_id="pkt-1",
        surety_id="osi",
        bond_data=_bound(),
        poa_record={"max_bond_value": 25000},
        docuseal=fake,
    )
    assert result["tenant_id"] == "shamrock"
    assert result["template_id"] == "1"
    assert len(fake.calls) == 1
    assert fake.calls[0]["template_id"] == "1"
    assert fake.calls[0]["packet_id"] == "pkt-1"
    assert fake.calls[0]["skip_bond_binding"] is False
