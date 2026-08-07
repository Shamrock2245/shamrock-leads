"""Unit tests for DocuSeal service helpers + SwipeSimple receipt parser."""
from __future__ import annotations

import hmac
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.services.docuseal_service import (
    DocuSealService,
    ROLE_INDEMNITOR,
    ROLE_DEFENDANT,
    ROLE_CO_INDEMNITOR,
    _safe_money,
    _amount_to_words,
    _number_to_words,
)
from dashboard.services.swipesimple_receipt_poller import parse_swipesimple_receipt


def test_prefill_values_from_bond():
    svc = DocuSealService(base_url="https://sign.example", api_key="test")
    vals = svc.prefill_values_from_bond(
        {
            "defendant_name": "Jane Doe",
            "indemnitor_name": "John Cosigner",
            "county": "Lee",
            "case_number": "26-CF-100",
            "poa_number": "OSI3 20134296",
            "booking_number": "BK123",
            "court_date": "",
            "indemnitor_email": "john@example.com",
            "bond_amount": 5000,
            "charge_details": [
                {"charge": "BATTERY", "bond_amount": 1000, "case_number": "26-CF-100", "poa_number": "POA1"},
                {"charge": "RESIST", "bond_amount": 1000, "case_number": "26-CF-100", "poa_number": "POA2"},
            ],
        }
    )
    assert vals["defendant_name"] == "Jane Doe"
    assert vals["IndemnitorName"] == "John Cosigner"
    assert vals["CaseNum"] == "26-CF-100"
    assert vals["PowerNum"] == "POA1" or vals["PowerNum"] == "OSI3 20134296"
    assert vals["CourtDate"] == "TBN"  # empty → TBN
    assert vals["today_day"]
    assert vals["today_month"]
    assert vals["today_year_2digit"]
    assert vals["offense_1"] == "BATTERY"
    assert vals["offense_2"] == "RESIST"
    assert vals["poa_number_1"] == "POA1"
    assert vals["poa_number_2"] == "POA2"
    # Premium: max(100, 100) + max(100, 100) = 200
    assert vals["numeric_premium"] == "200.00"
    assert "Two Hundred" in vals["written_premium"]
    assert vals["numeric_full_bond_amount"] == "5,000.00"
    assert vals["ssa_release_reason"]


def test_safe_money_edge_cases():
    assert _safe_money(None) == 0.0
    assert _safe_money("") == 0.0
    assert _safe_money("$1,250.50") == 1250.50
    assert _safe_money("n/a") == 0.0
    assert _safe_money({"x": 1}) == 0.0  # weird type
    assert _amount_to_words(5000) == "Five Thousand and 00/100"
    assert _number_to_words(0) == "Zero"
    assert _amount_to_words(None) == ""


def test_sign_url_for_slug_uses_public_url():
    svc = DocuSealService(base_url="https://sign.shamrockbailbonds.biz", api_key="k")
    assert svc.sign_url_for_slug("abc123") == "https://sign.shamrockbailbonds.biz/s/abc123"
    assert svc.sign_url_for_slug("") == ""


def test_normalize_create_response_list():
    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    raw = [
        {
            "id": 1,
            "submission_id": 99,
            "role": "Indemnitor",
            "email": "a@b.com",
            "slug": "slug1",
            "status": "sent",
        }
    ]
    out = svc.normalize_create_response(raw)
    assert out["submission_id"] == 99
    assert out["submitters"][0]["sign_url"].endswith("/s/slug1")
    assert out["submitters"][0]["role"] == "Indemnitor"


def test_build_submitter_defaults_email():
    s = DocuSealService.build_submitter(role=ROLE_INDEMNITOR, email="")
    assert "@shamrockbailbonds.biz" in s["email"]
    assert s["role"] == ROLE_INDEMNITOR
    assert s["send_email"] is False


def test_parse_swipesimple_receipt_bond_amount():
    body = """
    Thank you for your payment
    Amount: $350.00
    Customer: cosigner@example.com
    Transaction ID: TXN998877
    Booking #: LEE-999
    """
    p = parse_swipesimple_receipt("Payment receipt", body)
    assert p["amount"] == 350.0
    assert p["customer_email"] == "cosigner@example.com"
    assert p["transaction_id"] == "TXN998877"
    assert p["booking_number"] == "LEE-999"


def test_parse_swipesimple_skips_system_email():
    body = "Amount $100.00 from noreply@swipesimple.com and real@person.com"
    p = parse_swipesimple_receipt("rcpt", body)
    assert p["customer_email"] == "real@person.com"


@pytest.mark.asyncio
async def test_create_submission_for_packet_calls_api():
    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    fake_resp = [
        {
            "id": 10,
            "submission_id": 500,
            "role": ROLE_INDEMNITOR,
            "email": "ind@example.com",
            "slug": "aaa",
            "status": "sent",
        },
        {
            "id": 11,
            "submission_id": 500,
            "role": ROLE_DEFENDANT,
            "email": "def@example.com",
            "slug": "bbb",
            "status": "sent",
        },
    ]
    with patch.object(svc, "create_submission", new=AsyncMock(return_value=fake_resp)) as m:
        result = await svc.create_submission_for_packet(
            template_id=42,
            packet_id="pkt-1",
            bond_data={
                "defendant_name": "Def",
                "indemnitor_name": "Ind",
                "indemnitor_email": "ind@example.com",
                "defendant_email": "def@example.com",
            },
            send_email=False,
        )
        m.assert_awaited_once()
        kwargs = m.await_args.kwargs
        assert kwargs["template_id"] == 42
        assert kwargs["send_email"] is False
        roles = [s["role"] for s in kwargs["submitters"]]
        assert ROLE_INDEMNITOR in roles
        assert ROLE_DEFENDANT in roles
        assert result["submission_id"] == 500
        assert len(result["submitters"]) == 2


@pytest.mark.asyncio
async def test_multi_cosigner_roles():
    """Indemnitor + Co-Indemnitor + Defendant — no dropped links."""
    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    fake = [
        {"id": 1, "submission_id": 9, "role": ROLE_INDEMNITOR, "slug": "a", "email": "a@x.com"},
        {"id": 2, "submission_id": 9, "role": ROLE_CO_INDEMNITOR, "slug": "b", "email": "b@x.com"},
        {"id": 3, "submission_id": 9, "role": ROLE_DEFENDANT, "slug": "c", "email": "c@x.com"},
    ]
    with patch.object(svc, "create_submission", new=AsyncMock(return_value=fake)) as m:
        result = await svc.create_submission_for_packet(
            template_id=1,
            packet_id="pkt-multi",
            bond_data={"defendant_name": "D", "defendant_email": "d@x.com"},
            indemnitors=[
                {"name": "A", "email": "a@x.com"},
                {"name": "B", "email": "b@x.com"},
            ],
        )
        roles = [s["role"] for s in m.await_args.kwargs["submitters"]]
        assert roles == [ROLE_INDEMNITOR, ROLE_CO_INDEMNITOR, ROLE_DEFENDANT]
        assert len(result["submitters"]) == 3
        assert all(s.get("sign_url") for s in result["submitters"])


def test_prefill_never_raises_on_garbage():
    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    # Must not raise KeyError / TypeError / ValueError
    vals = svc.prefill_values_from_bond(
        {
            "bond_amount": None,
            "charges": [None, {"bond_amount": "nope"}, "THEFT"],
            "poa_number": None,
            "defendant": "not-a-dict",
            "indemnitor": None,
            "premium_amount": "",
        }
    )
    assert isinstance(vals, dict)


def test_verify_docuseal_signature():
    from dashboard.routers.webhooks import verify_docuseal_signature

    secret = "test-secret"
    body = b'{"event_type":"submission.completed"}'
    with patch.dict(os.environ, {"DOCUSEAL_WEBHOOK_SECRET": secret, "DEBUG": "false"}):
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_docuseal_signature(body, sig) is True
        assert verify_docuseal_signature(body, "deadbeef") is False
        assert verify_docuseal_signature(body, f"sha256={sig}") is True


def test_resolve_template_id_for_surety():
    from dashboard.services.docuseal_service import resolve_template_id_for_surety

    with patch.dict(
        os.environ,
        {
            "DOCUSEAL_TEMPLATE_ID": "99",
            "DOCUSEAL_TEMPLATE_ID_OSI": "11",
            "DOCUSEAL_TEMPLATE_ID_PALMETTO": "22",
        },
        clear=False,
    ):
        assert resolve_template_id_for_surety("osi") == "11"
        assert resolve_template_id_for_surety("palmetto") == "22"
        assert resolve_template_id_for_surety("unknown") == "11"

    # Palmetto must not silently use OSI template
    with patch.dict(
        os.environ,
        {
            "DOCUSEAL_TEMPLATE_ID": "1",
            "DOCUSEAL_TEMPLATE_ID_OSI": "1",
            "DOCUSEAL_TEMPLATE_ID_PALMETTO": "",
        },
        clear=False,
    ):
        assert resolve_template_id_for_surety("osi") == "1"
        assert resolve_template_id_for_surety("palmetto") is None


def test_build_bond_data_from_dashboard_merges_charges():
    from dashboard.services.docuseal_service import (
        build_bond_data_from_dashboard,
        DocuSealService,
    )

    bond = build_bond_data_from_dashboard(
        ctx={
            "county": "Lee",
            "bond_amount": 5000,
            "defendant": {"name": "Def Person", "address": "1 Main"},
            "indemnitor": {"name": "Ind Person", "email": "i@x.com", "phone": "2395551212"},
            "charges": "OLD TEXT",
        },
        body={
            "charge_details": [
                {"charge": "BATTERY", "bond_amount": 2500, "case_number": "26-CF-1", "poa_number": "P1"},
                {"charge": "RESIST", "bond_amount": 2500, "case_number": "26-CF-1", "poa_number": "P2"},
            ],
            "poa_number": "P1",
        },
        field_overrides={"case_number": "26-CF-1"},
        surety_id="osi",
    )
    vals = DocuSealService.prefill_values_from_bond(bond)
    assert vals["offense_1"] == "BATTERY"
    assert vals["poa_number_2"] == "P2"
    assert vals["CaseNum"] == "26-CF-1"
    assert vals["numeric_premium"] == "500.00"


@pytest.mark.asyncio
async def test_default_esign_provider_is_docuseal():
    from dashboard.services.packet_builder_service import resolve_client_esign_provider

    with patch.dict(os.environ, {"DEFAULT_ESIGN_PROVIDER": "docuseal"}, clear=False):
        # No indemnitor/defendant/bond IDs → env default, no DB
        p = await resolve_client_esign_provider(preferred=None)
        assert p == "docuseal"
        p2 = await resolve_client_esign_provider(preferred="docuseal")
        assert p2 == "docuseal"
        p3 = await resolve_client_esign_provider(preferred="signnow")
        assert p3 == "signnow"
