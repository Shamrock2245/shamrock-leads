"""
Offline unit tests for SwipeSimple Share Invoice service.

No network. Never enables SWIPESIMPLE_LIVE for real HTTP.
No BlueBubbles / email customer sends.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services.swipesimple_invoice_service import (
    SwipeSimpleInvoiceError,
    SwipeSimpleLiveDisabled,
    amounts_equal,
    build_create_invoice_form,
    build_dispatch_payload,
    create_locked_invoice,
    default_smoke_reference_id,
    dispatch_invoice,
    dispatch_live_enabled,
    live_http_enabled,
    money_to_decimal,
    new_invoice_path,
    premium_dollars_to_cents,
    smoke_create_one_cent_draft,
    _parse_authenticity_token,
)


@pytest.fixture(autouse=True)
def _clear_live_env(monkeypatch):
    for key in (
        "SWIPESIMPLE_LIVE",
        "SWIPESIMPLE_DISPATCH_LIVE",
        "SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE",
        "SWIPESIMPLE_SESSION",
        "SWIPESIMPLE_COOKIE_JAR",
        "SWIPESIMPLE_CSRF_TOKEN",
        "SWIPESIMPLE_NEW_INVOICE_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


def test_premium_dollars_to_cents_exact():
    assert premium_dollars_to_cents(Decimal("1.00")) == 100
    assert premium_dollars_to_cents(Decimal("1500.00")) == 150000
    assert premium_dollars_to_cents(Decimal("0.01")) == 1


def test_premium_dollars_to_cents_fail_closed_fractional():
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_not_exact_cents"):
        premium_dollars_to_cents(Decimal("10.001"))


def test_premium_dollars_to_cents_fail_closed_negative():
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_negative"):
        premium_dollars_to_cents(Decimal("-5.00"))


def test_build_create_invoice_form_shape_and_empty_customer_id():
    fields = build_create_invoice_form(
        authenticity_token="TOKEN",
        merchant_account_id="acc_bd9fed047bd6f7c6",
        booking_number="LEE-2026-0001",
        cents=150000,
        customer={
            "customer_id": "",
            "name": "Pat Indemnitor",
            "email": "pat@example.com",
            "phone": "2395550100",
        },
    )
    as_dict = dict(fields)
    assert as_dict["authenticity_token"] == "TOKEN"
    assert as_dict["invoice[merchant_account_id]"] == "acc_bd9fed047bd6f7c6"
    assert as_dict["invoice[customer][id]"] == ""
    assert as_dict["customer-proxy"] == "-Pat Indemnitor"  # select2 createTag for new customer
    assert as_dict["invoice[reference_id]"] == "LEE-2026-0001"
    assert as_dict["invoice[items][][id]"] == "im_bae23df0a0cb4e01a688bdd75cc96bf1"
    assert as_dict["invoice[items][][name]"] == 'Bail Bond Premium'
    assert as_dict["invoice[items][][quantity]"] == "1"
    assert as_dict["invoice[items][][price]"] == "150000"
    assert as_dict["invoice[amount]"] == "150000"
    assert as_dict["invoice[unadjusted_amount]"] == "150000"
    assert as_dict["invoice[save_as_draft]"] == "false"
    assert as_dict["invoice[invoice_email][web_link]"] == "1"
    assert as_dict["invoice[invoice_email][cc_self]"] == "0"
    assert "invoice[invoice_email][email]" not in as_dict
    assert "invoice[invoice_email][phone]" not in as_dict
    assert as_dict["invoice[customer][name]"] == "Pat Indemnitor"
    assert as_dict["invoice[customer][email]"] == "pat@example.com"
    assert as_dict["invoice[customer][phone]"] == "2395550100"
    keys = [k for k, _ in fields]
    assert "invoice[email]" in keys
    assert "invoice[phone]" in keys


def test_live_http_gate_default_off():
    assert live_http_enabled() is False
    assert dispatch_live_enabled() is False


def test_new_invoice_path_env(monkeypatch):
    assert new_invoice_path() == "/invoices/new"
    monkeypatch.setenv("SWIPESIMPLE_NEW_INVOICE_PATH", "invoices/new")
    assert new_invoice_path() == "/invoices/new"
    monkeypatch.setenv("SWIPESIMPLE_NEW_INVOICE_PATH", "/billing/invoices/new/")
    assert new_invoice_path() == "/billing/invoices/new"


def test_csrf_html_parse_input_and_meta():
    html = '<form><input type="hidden" name="authenticity_token" value="tokA" /></form>'
    assert _parse_authenticity_token(html) == "tokA"
    html2 = '<meta name="csrf-token" content="tokB" />'
    assert _parse_authenticity_token(html2) == "tokB"
    assert _parse_authenticity_token("") is None


@pytest.mark.asyncio
async def test_create_locked_invoice_idempotent_short_circuit():
    bond = {
        "bond_id": "BOND-1",
        "booking_number": "BK-100",
        "premium": "250.00",
        "swipesimple_payment_link": "https://swipesimple.com/pay/abc",
        "swipesimple_invoice_bond_id": "BOND-1",
        "swipesimple_invoice_number": "BK-100",
        "swipesimple_invoice_id": "inv_existing",
    }
    with patch(
        "dashboard.services.swipesimple_invoice_service._load_bond_by_id",
        new_callable=AsyncMock,
        return_value=bond,
    ), patch(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        new_callable=AsyncMock,
    ) as share:
        result = await create_locked_invoice("BOND-1")
        assert result["ok"] is True
        assert result["idempotent"] is True
        assert result["payment_link"] == "https://swipesimple.com/pay/abc"
        assert result["invoice_number"] == "BK-100"
        share.assert_not_called()


@pytest.mark.asyncio
async def test_create_locked_invoice_live_gate_blocks_http():
    bond = {
        "bond_id": "BOND-2",
        "booking_number": "BK-200",
        "premium": "100.00",
        "indemnitor_name": "Alex",
        "indemnitor_phone": "2395550199",
    }
    with patch(
        "dashboard.services.swipesimple_invoice_service._load_bond_by_id",
        new_callable=AsyncMock,
        return_value=bond,
    ):
        with pytest.raises((SwipeSimpleLiveDisabled, SwipeSimpleInvoiceError)):
            await create_locked_invoice("BOND-2")


@pytest.mark.asyncio
async def test_create_locked_invoice_unresolved_blocks_duplicate():
    bond = {
        "bond_id": "BOND-3",
        "booking_number": "BK-300",
        "premium": "100.00",
        "swipesimple_invoice_unresolved": True,
        "swipesimple_invoice_number": "BK-300",
        "swipesimple_invoice_bond_id": "BOND-3",
    }
    with patch(
        "dashboard.services.swipesimple_invoice_service._load_bond_by_id",
        new_callable=AsyncMock,
        return_value=bond,
    ):
        with pytest.raises(SwipeSimpleInvoiceError, match="pending_id_resolution"):
            await create_locked_invoice("BOND-3")


@pytest.mark.asyncio
async def test_dispatch_invoice_dry_run_does_not_send():
    bond = {
        "bond_id": "BOND-4",
        "booking_number": "BK-400",
        "premium": "75.00",
        "defendant_name": "Jamie Doe",
        "indemnitor_phone": "2395550111",
        "indemnitor_email": "jamie@example.com",
        "swipesimple_payment_link": "https://swipesimple.com/pay/xyz",
    }
    with patch(
        "dashboard.services.swipesimple_invoice_service._load_bond_by_id",
        new_callable=AsyncMock,
        return_value=bond,
    ), patch(
        "dashboard.services.bb_client.send_message_universal",
        new_callable=AsyncMock,
    ) as bb_send:
        result = await dispatch_invoice("BOND-4", channel="imessage")
        assert result["ok"] is True
        assert result["sent"] is False
        assert result.get("dry_run") is True or result.get("stub") is True
        assert result["booking_number"] == "BK-400"
        bb_send.assert_not_called()


def test_build_dispatch_payload_includes_booking_and_link():
    bond = {
        "defendant_name": "Jamie Doe",
        "indemnitor_phone": "2395550111",
        "indemnitor_email": "jamie@example.com",
    }
    payload = build_dispatch_payload(
        bond,
        payment_link="https://swipesimple.com/pay/xyz",
        booking_number="BK-400",
        premium=Decimal("75.00"),
        channel="imessage",
    )
    assert "BK-400" in payload["body"]
    assert "https://swipesimple.com/pay/xyz" in payload["body"]
    assert "75.00" in payload["body"]
    assert payload["has_recipient"] is True
    assert payload["phone"] == "2395550111"


def test_amounts_equal_and_money_to_decimal():
    assert amounts_equal("100.00", Decimal("100"))
    assert money_to_decimal("$1,250.50") == Decimal("1250.50")
    assert money_to_decimal("nope") is None


def test_default_smoke_reference_id_shape():
    ref = default_smoke_reference_id()
    assert ref.startswith("SMOKE-")
    assert len(ref) >= len("SMOKE-YYYYMMDD-HHMM")


@pytest.mark.asyncio
async def test_smoke_check_only_no_http(monkeypatch):
    monkeypatch.delenv("SWIPESIMPLE_LIVE", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_SESSION", raising=False)
    with patch(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        new_callable=AsyncMock,
    ) as share:
        result = await smoke_create_one_cent_draft(check_only=True)
        assert result["ok"] is True
        assert result["check_only"] is True
        assert result["ready"] is False
        assert result["gates"]["live_enabled"] is False
        share.assert_not_called()


@pytest.mark.asyncio
async def test_smoke_live_gate_blocks_without_live():
    with patch(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        new_callable=AsyncMock,
    ) as share:
        with pytest.raises((SwipeSimpleLiveDisabled, SwipeSimpleInvoiceError)):
            await smoke_create_one_cent_draft(reference_id="SMOKE-20260924-1317")
        share.assert_not_called()


@pytest.mark.asyncio
async def test_smoke_rejects_non_smoke_reference():
    with pytest.raises(SwipeSimpleInvoiceError, match="smoke_reference_id_must_start_with_SMOKE"):
        await smoke_create_one_cent_draft(reference_id="LEE-REAL-BOOKING")


@pytest.mark.asyncio
async def test_smoke_requires_session_when_live(monkeypatch):
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    with patch(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        new_callable=AsyncMock,
    ) as share:
        with pytest.raises(SwipeSimpleInvoiceError, match="session_not_configured"):
            await smoke_create_one_cent_draft(reference_id="SMOKE-20260924-1317")
        share.assert_not_called()
