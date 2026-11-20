"""Write Bond prefill defects from the no-send DocuSeal dry run.

Fake TEST- names only. Nothing here calls DocuSeal or sends a packet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services.docuseal_service import DocuSealService
from dashboard.services.packet_builder_service import build_adaptive_field_map


def _prefill(bond):
    return DocuSealService.prefill_values_from_bond(bond)


def test_second_poa_is_kept_when_poa_number_is_also_set():
    values = _prefill({
        "defendant_name": "TEST-Doe, John",
        "surety_id": "osi",
        "poa_number": "A",
        "poa_numbers": ["A", "B"],
    })
    assert values["all_poa_numbers"] == "A, B"
    assert values["bond_numbers"] == "A, B"
    assert values["poa_numbers"] == "A, B"
    assert values["BondNumbers"] == "A, B"


def test_poa_list_order_is_list_then_charge_rows_then_singular():
    values = _prefill({
        "defendant_name": "TEST-Doe, John",
        "surety_id": "osi",
        "poa_numbers": ["TEST-A"],
        "poa_number": "TEST-B",
        "charge_details": [
            {"charge": "TEST CHARGE", "poa_number": "TEST-C", "bond_amount": 1000},
            {"charge": "TEST CHARGE TWO", "poa_number": "TEST-A", "bond_amount": 1000},
        ],
    })
    assert values["all_poa_numbers"] == "TEST-A, TEST-C, TEST-B"
    assert values["bond_numbers"] == "TEST-A, TEST-C, TEST-B"
    assert values["poa_numbers"] == "TEST-A, TEST-C, TEST-B"


@pytest.mark.parametrize("raw", [
    "John Q TEST-Doe",
    "TEST-Doe, John Quincy",
    "TEST-DOE, JOHN Q",
])
def test_defendant_name_is_last_first_without_middle(raw):
    values = _prefill({"defendant_name": raw, "surety_id": "osi"})
    assert values["defendant_name"] == "TEST-Doe, John"
    assert values["DefName"] == "TEST-Doe, John"


def test_structured_first_and_last_win_over_the_roster_string():
    values = _prefill({
        "defendant_name": "Roster Name Ignored",
        "surety_id": "osi",
        "defendant": {
            "first_name": "John",
            "middle_name": "Quincy",
            "last_name": "TEST-Doe",
        },
    })
    assert values["defendant_name"] == "TEST-Doe, John"
    assert values["DefName"] == "TEST-Doe, John"


def test_all_caps_names_keep_hyphens_apostrophes_and_mc():
    mcdonald = _prefill({"defendant_name": "TEST-MCDONALD, JOHN Q", "surety_id": "osi"})
    assert mcdonald["defendant_name"] == "TEST-McDonald, John"
    assert mcdonald["DefName"] == "TEST-McDonald, John"
    oneal = _prefill({"defendant_name": "TEST-O'NEAL, JOHN Q", "surety_id": "osi"})
    assert oneal["defendant_name"] == "TEST-O'Neal, John"
    assert oneal["DefName"] == "TEST-O'Neal, John"
    mixed = _prefill({"defendant_name": "TEST-McDonald, John Quincy", "surety_id": "osi"})
    assert mixed["defendant_name"] == "TEST-McDonald, John"


@pytest.mark.parametrize("raw, expected", [
    ("Circuit Court", "Circuit"),
    ("County Court", "County"),
    ("Circuit", "Circuit"),
])
def test_court_type_drops_a_trailing_court_word(raw, expected):
    values = _prefill({
        "defendant_name": "TEST-Doe, John",
        "surety_id": "osi",
        "court_type": raw,
    })
    assert values["court_type"] == expected
    assert values["CourtType"] == expected


def test_printed_dates_use_america_new_york(monkeypatch):
    frozen = datetime(2026, 11, 21, 1, 30, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen.replace(tzinfo=None)
            return frozen.astimezone(tz)

    monkeypatch.setattr(
        "dashboard.services.docuseal_service.datetime",
        _FrozenDateTime,
    )
    values = _prefill({"defendant_name": "TEST-Doe, John", "surety_id": "osi"})
    assert values["today_date"] == "11/20/2026"
    assert values["today_date_long"] == "November 20, 2026"
    assert values["date"] == "November 20, 2026"

    fields = build_adaptive_field_map({
        "defendant": {"name": "TEST-Doe, John"},
        "indemnitor": {"name": "TEST-Indemnitor"},
        "surety_id": "osi",
    })
    assert fields["today_date"] == "11/20/2026"
    assert fields["today_date_long"] == "November 20, 2026"


@pytest.mark.asyncio
async def test_packet_submission_sets_send_email_and_send_sms_false(monkeypatch):
    async def _ok(*_args, **_kwargs):
        return {"ok": True, "exempt": "test_fixture", "issues": [], "methods": ["scan"]}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )
    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")
    fake = [
        {"id": 1, "submission_id": 9, "role": "indemnitor", "slug": "a", "email": "indemnitor@example.invalid"},
        {"id": 2, "submission_id": 9, "role": "defendant", "slug": "b", "email": "defendant@example.invalid"},
    ]
    with patch.object(svc, "create_submission", new=AsyncMock(return_value=fake)) as created:
        await svc.create_submission_for_packet(
            template_id=1,
            packet_id="PKT-TEST-PREFILL",
            bond_data={
                "bond_case_id": "TEST-BOND",
                "match_id": "TEST-MATCH",
                "match_status": "validated",
                "defendant_id": "TEST-DEF",
                "indemnitor_id": "TEST-IND",
                "case_number": "TEST-CASE",
                "poa_number": "TEST-POA-A",
                "poa_numbers": ["TEST-POA-A", "TEST-POA-B"],
                "booking_number": "TEST-BOOK",
                "surety_id": "osi",
                "include_bondsman": False,
                "defendant_name": "John Q TEST-Doe",
                "indemnitor_name": "TEST-Indemnitor",
                "indemnitor": {"name": "TEST-Indemnitor", "email": "indemnitor@example.invalid"},
                "defendant": {"name": "John Q TEST-Doe", "email": "defendant@example.invalid"},
            },
            send_email=False,
        )
    kwargs = created.await_args.kwargs
    assert kwargs["send_email"] is False
    assert kwargs["send_sms"] is False
    submitters = kwargs["submitters"]
    assert submitters
    for submitter in submitters:
        assert submitter["send_email"] is False
        assert submitter["send_sms"] is False
    assert submitters[0]["values"]["all_poa_numbers"] == "TEST-POA-A, TEST-POA-B"
    assert submitters[0]["values"]["defendant_name"] == "TEST-Doe, John"
