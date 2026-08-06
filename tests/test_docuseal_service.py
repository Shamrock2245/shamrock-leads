"""Unit tests for DocuSeal service helpers + SwipeSimple receipt parser."""
from __future__ import annotations

import hmac
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.services.docuseal_service import DocuSealService, ROLE_INDEMNITOR, ROLE_DEFENDANT
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
        }
    )
    assert vals["defendant_name"] == "Jane Doe"
    assert vals["IndemnitorName"] == "John Cosigner"
    assert vals["CaseNum"] == "26-CF-100"
    assert vals["PowerNum"] == "OSI3 20134296"
    assert vals["CourtDate"] == "TBN"  # empty → TBN


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


def test_verify_docuseal_signature():
    from dashboard.routers.webhooks import verify_docuseal_signature

    secret = "test-secret"
    body = b'{"event_type":"submission.completed"}'
    with patch.dict(os.environ, {"DOCUSEAL_WEBHOOK_SECRET": secret, "DEBUG": "false"}):
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_docuseal_signature(body, sig) is True
        assert verify_docuseal_signature(body, "deadbeef") is False
        assert verify_docuseal_signature(body, f"sha256={sig}") is True
