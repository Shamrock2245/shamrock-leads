"""Financed installment invoices and gateway posting. No network."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers import payment_plans as pp
from dashboard.routers.webhooks import webhooks_bp
from dashboard.services.installment_invoice_service import stage_due_installments
from dashboard.services.missed_payment_alert_service import MissedPaymentAlertService
from dashboard.services.plan_payment_poster import post_gateway_event
from dashboard.services.swipesimple_receipt_poller import (
    SwipeSimpleReceiptPoller,
    parse_swipesimple_receipt,
)
from tests._inmem_mongo import FakeCollection
from tests.test_payment_plan_link_core import TxnClient
from tests.test_payment_plan_pay_dedupe import DB

PLAN = "plan-inst-1"
BOOKING = "BK100"


def _installments(today: str):
    later = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    rows = []
    for number, due in enumerate((today, later, later, later), start=1):
        rows.append({
            "n": number,
            "amount": "250.00",
            "amount_cents": 25000,
            "due_date": due if number > 1 else today,
            "status": "scheduled",
        })
    return rows


def _plan(today: str) -> dict:
    return {
        "plan_id": PLAN,
        "booking_number": BOOKING,
        "status": "active",
        "source": "premium_confirmation",
        "balance_remaining": 1000.0,
        "balance_remaining_cents": 100000,
        "total_paid": 0.0,
        "payments_made": 0,
        "frequency": "weekly",
        "total_premium": "1500.00",
        "installments": _installments(today),
    }


def _wire(db, monkeypatch):
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    pp._payment_claim_index_ready = False
    client = TxnClient(db)

    async def _txn():
        return client

    def _col(name):
        return db[name]

    monkeypatch.setattr(pp, "get_collection", _col)
    monkeypatch.setattr(pp, "_txn_client", _txn)
    monkeypatch.setattr("dashboard.services.plan_payment_poster.get_collection", _col)
    monkeypatch.setattr("dashboard.services.installment_invoice_service.get_collection", _col)
    monkeypatch.setattr("dashboard.routers.webhooks.get_collection", _col)
    monkeypatch.delenv("SWIPESIMPLE_LIVE", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_DISPATCH_LIVE", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_LEADS", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)


def _paid(txn="TX1", booking=BOOKING, reference=f"{BOOKING}-I1", amount="250.00", plan_id=PLAN):
    return {
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": txn,
        "amount": amount,
        "custom_fields": {
            "booking_number": booking,
            "reference_id": reference,
            "plan_id": plan_id,
        },
    }


def _cents(db) -> int:
    return db["payment_plans"].docs[0]["balance_remaining_cents"]


def _audits(db, event_type: str) -> list:
    return [row for row in db["audit_events"].docs if row.get("event_type") == event_type]


@pytest.fixture
def db(monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()
    store = DB()
    store["payment_plans"].docs.append(_plan(today))
    _wire(store, monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        yield store
    pp._inflight_txns.clear()


def test_due_installment_stages_once_as_dry_run(db, monkeypatch):
    async def _http(**kwargs):
        raise AssertionError("SwipeSimple HTTP is not allowed while LIVE is off")

    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        _http,
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.live_http_enabled",
        lambda: False,
    )

    first = asyncio.run(stage_due_installments())
    second = asyncio.run(stage_due_installments())

    assert first["staged"] == 1 and first["dry_run"] is True
    assert second["staged"] == 0
    claims = db["installment_invoice_claims"].docs
    assert len(claims) == 1
    assert claims[0]["amount_cents"] == 25000
    assert claims[0]["reference"] == f"{BOOKING}-I1"
    assert claims[0]["status"] == "dry_run"
    row = db["payment_plans"].docs[0]["installments"][0]
    assert row["status"] == "invoiced"
    assert row["invoice_dry_run"] is True
    assert row["invoice_amount_cents"] == 25000
    assert db["payment_plans"].docs[0]["installments"][1]["status"] == "scheduled"


def test_live_without_session_does_not_mark_sent(db, monkeypatch):
    async def _http(**kwargs):
        raise AssertionError("HTTP must not run without a session")

    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        _http,
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.live_http_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.load_swipesimple_session_config",
        lambda: {"has_session": False, "has_cookie_jar": False},
    )

    result = asyncio.run(stage_due_installments())
    assert result["failed"] == 1 and result["staged"] == 0
    assert db["installment_invoice_claims"].docs == []
    assert db["payment_plans"].docs[0]["installments"][0]["status"] == "scheduled"
    assert "invoice_key" not in db["payment_plans"].docs[0]["installments"][0]


def test_staff_test_booking_does_not_http_when_live(db, monkeypatch):
    db["payment_plans"].docs[0]["booking_number"] = "TEST-BK"
    called = {"n": 0}

    async def _http(**kwargs):
        called["n"] += 1
        raise AssertionError("staff test HTTP")

    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        _http,
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.live_http_enabled",
        lambda: True,
    )
    monkeypatch.delenv("SWIPESIMPLE_TEST_CASE_INVOICES", raising=False)

    result = asyncio.run(stage_due_installments())
    assert result["blocked"] == 1
    assert called["n"] == 0
    assert db["installment_invoice_claims"].docs[0]["status"] == "staff_test_blocked"


def test_webhook_posts_installment_once_and_rejects_other_booking(db, monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    app = FastAPI()
    app.include_router(webhooks_bp)
    client = TestClient(app)

    mismatch = client.post("/api/webhooks/payment", json=_paid(txn="TX-OTHER", booking="OTHER"))
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "booking_mismatch"
    assert _cents(db) == 100000
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs == []
    assert db["audit_events"].docs == []

    paid = client.post("/api/webhooks/payment", json=_paid())
    assert paid.status_code == 200
    assert _cents(db) == 75000
    assert len(db["payments"].docs) == 1
    assert _audits(db, "payment_plan_payment_applied") == _audits(db, "payment_plan_payment_applied")
    assert len(_audits(db, "payment_plan_payment_applied")) == 1
    assert db["payment_plans"].docs[0]["installments"][0]["status"] == "paid"

    replay = client.post("/api/webhooks/payment", json=_paid())
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert _cents(db) == 75000
    assert len(db["payments"].docs) == 1
    assert len(_audits(db, "payment_plan_payment_applied")) == 1


def test_down_payment_does_not_reduce_financed_balance(db, monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    app = FastAPI()
    app.include_router(webhooks_bp)
    client = TestClient(app)
    resp = client.post("/api/webhooks/payment", json={
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": "TX-DOWN",
        "amount": "500.00",
        "custom_fields": {"booking_number": BOOKING, "plan_id": PLAN},
    })
    assert resp.status_code == 200
    assert _cents(db) == 100000
    assert len(db["payments"].docs) == 1
    assert "plan_id" not in db["payments"].docs[0]
    assert db["audit_events"].docs == [] or all(
        row.get("event_type") != "payment_plan_payment_applied" for row in db["audit_events"].docs
    )


def test_poller_posts_the_same_installment_once(db):
    poller = SwipeSimpleReceiptPoller(db=db)
    parsed = {
        "amount": "250.00",
        "booking_number": BOOKING,
        "reference_id": f"{BOOKING}-I1",
        "transaction_id": "TX-POLL",
        "customer_email": None,
    }

    async def go():
        first = await poller._apply_bond_payment(
            db, parsed, gmail_message_id="m1", raw_subject="receipt",
        )
        second = await poller._apply_bond_payment(
            db, parsed, gmail_message_id="m1", raw_subject="receipt",
        )
        return first, second

    first, second = asyncio.run(go())
    assert first["success"] is True and first["plan_applied"] is True
    assert second["idempotent"] is True
    assert _cents(db) == 75000
    assert len(db["payments"].docs) == 1
    assert len(_audits(db, "payment_plan_payment_applied")) == 1


def test_refund_restores_balance_once_and_decline_does_not(db):
    async def go():
        paid = await post_gateway_event(_paid(txn="TX-REF"), actor="swipesimple_webhook")
        after_paid = _cents(db)
        declined = await post_gateway_event({
            "event_type": "payment.failed",
            "status": "declined",
            "transaction_id": "TX-NO",
            "amount": "250.00",
            "custom_fields": {
                "booking_number": BOOKING,
                "reference_id": f"{BOOKING}-I2",
                "plan_id": PLAN,
            },
        }, actor="swipesimple_webhook")
        again = await post_gateway_event({
            "event_type": "payment.failed",
            "status": "declined",
            "transaction_id": "TX-NO",
            "amount": "250.00",
            "custom_fields": {
                "booking_number": BOOKING,
                "reference_id": f"{BOOKING}-I2",
                "plan_id": PLAN,
            },
        }, actor="swipesimple_webhook")
        refund = await post_gateway_event({
            "event_type": "payment.refunded",
            "status": "refunded",
            "transaction_id": "TX-REF",
            "amount": "250.00",
            "custom_fields": {
                "booking_number": BOOKING,
                "reference_id": f"{BOOKING}-I1",
                "plan_id": PLAN,
            },
        }, actor="swipesimple_webhook")
        refund_again = await post_gateway_event({
            "event_type": "payment.refunded",
            "status": "refunded",
            "transaction_id": "TX-REF",
            "amount": "250.00",
            "custom_fields": {
                "booking_number": BOOKING,
                "reference_id": f"{BOOKING}-I1",
                "plan_id": PLAN,
            },
        }, actor="swipesimple_webhook")
        return paid, after_paid, declined, again, refund, refund_again

    paid, after_paid, declined, again, refund, refund_again = asyncio.run(go())
    assert paid["handled"] is True and after_paid == 75000
    assert declined["balance_unchanged"] is True
    assert again["idempotent"] is True
    assert len(_audits(db, "payment_plan_payment_failed")) == 1
    assert refund["handled"] is True
    assert refund_again["idempotent"] is True
    assert _cents(db) == 100000
    assert db["payments"].docs[0]["status"] == "reversed"
    assert db["payment_plans"].docs[0]["installments"][0]["status"] == "reversed"
    assert len(_audits(db, "payment_plan_payment_reversed")) == 1


def test_receipt_parser_keeps_installment_reference():
    parsed = parse_swipesimple_receipt(
        "Payment receipt",
        "Amount: $250.00\nBooking #: BK100\nInvoice BK100-I1\nTransaction ID: TX9\n",
    )
    assert parsed["amount"] == 250.0
    assert parsed["booking_number"] == "BK100"
    assert parsed["reference_id"] == "BK100-I1"
    assert parsed["transaction_id"] == "TX9"


def test_overdue_installment_alerts_once_via_bluebubbles(monkeypatch):
    cols: dict[str, FakeCollection] = {}

    def _col(name):
        cols.setdefault(name, FakeCollection())
        return cols[name]

    due = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _col("payment_plans").docs.append({
        "_id": "plan1",
        "plan_id": PLAN,
        "booking_number": BOOKING,
        "status": "active",
        "next_due_date": due,
        "installment_amount": 250,
        "defendant_name": "Test Person",
        "defendant_phone": "2395550100",
    })
    monkeypatch.setattr(
        "dashboard.services.missed_payment_alert_service.get_collection",
        _col,
    )
    sends = {"n": 0}

    async def _send(phone, message):
        sends["n"] += 1
        assert "Twilio" not in message
        return True

    async def _geo(*_a, **_k):
        return ""

    svc = MissedPaymentAlertService()
    svc._send_bb = _send
    svc._make_geo_link = _geo
    with patch("dashboard.routers.events.emit_event", new=AsyncMock()):
        first = asyncio.run(svc.scan_and_alert())
        second = asyncio.run(svc.scan_and_alert())
    assert first["alerted"] == 1
    assert second["alerted"] == 0
    assert sends["n"] == 1
    assert len(cols["missed_payment_alerts"].docs) == 1


def test_confirmation_writer_string_balances_take_a_paid_installment(db, monkeypatch):
    """Plans from ensure_payment_plan_for_confirmation store decimal strings.

    Posting coerces those strings to numbers, then ``$inc``s them.
    ``balance_remaining_cents`` stays the integer source of truth.
    """
    from decimal import Decimal

    from dashboard.services import payment_plan_creation as creation

    db["payment_plans"].docs.clear()
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-1",
        "booking_number": BOOKING,
        "tenant_id": "shamrock",
        "surety_id": "osi",
        "premium_confirmed_amount": "1500.00",
        "down_payment_amount": "500.00",
    })
    monkeypatch.setattr(creation, "get_collection", lambda name: db[name])

    async def go():
        created = await creation.ensure_payment_plan_for_confirmation(
            tenant_id="shamrock",
            bond_case_id="BC-1",
            confirmation_id="pcf-1",
            financed=Decimal("1000.00"),
            installments=4,
            frequency="weekly",
            actor="office@example.invalid",
            session=object(),
        )
        plan_id = created["plan_id"]
        written = db["payment_plans"].docs[0]
        assert written["balance_remaining"] == "1000.00"
        assert written["total_paid"] == "0.00"
        assert written["balance_remaining_cents"] == 100000
        assert type(written["balance_remaining_cents"]) is int
        # The dollar string is not the truth when it disagrees with cents.
        written["balance_remaining"] = "900.00"
        paid = await post_gateway_event(_paid(plan_id=plan_id), actor="swipesimple_webhook")
        after = {
            "balance_remaining": db["payment_plans"].docs[0]["balance_remaining"],
            "balance_remaining_cents": db["payment_plans"].docs[0]["balance_remaining_cents"],
            "total_paid": db["payment_plans"].docs[0]["total_paid"],
            "total_paid_cents": db["payment_plans"].docs[0]["total_paid_cents"],
            "payments_made": db["payment_plans"].docs[0]["payments_made"],
            "status": db["payment_plans"].docs[0]["status"],
        }
        again = await post_gateway_event(_paid(plan_id=plan_id), actor="swipesimple_webhook")
        refund = await post_gateway_event({
            "event_type": "payment.refunded",
            "status": "refunded",
            "transaction_id": "TX1",
            "amount": "250.00",
            "custom_fields": {
                "booking_number": BOOKING,
                "reference_id": f"{BOOKING}-I1",
                "plan_id": plan_id,
            },
        }, actor="swipesimple_webhook")
        return paid, after, again, refund

    paid, after, again, refund = asyncio.run(go())
    plan = db["payment_plans"].docs[0]
    assert paid["handled"] is True and paid.get("success") is True
    assert after == {
        "balance_remaining": 750.0,
        "balance_remaining_cents": 75000,
        "total_paid": 250.0,
        "total_paid_cents": 25000,
        "payments_made": 1,
        "status": "active",
    }
    assert type(after["balance_remaining"]) is float
    assert type(after["total_paid"]) is float
    assert type(after["balance_remaining_cents"]) is int
    assert again["idempotent"] is True
    assert again["balance_remaining"] == 750.0
    assert refund["handled"] is True and refund["idempotent"] is False
    assert plan["balance_remaining"] == 1000.0
    assert plan["balance_remaining_cents"] == 100000
    assert plan["total_paid"] == 0.0
    assert plan["total_paid_cents"] == 0
    assert type(plan["balance_remaining_cents"]) is int
    assert type(plan["total_paid_cents"]) is int
    assert plan["status"] == "active"
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["status"] == "reversed"


def test_string_balance_pays_off_from_integer_cents(db):
    plan = db["payment_plans"].docs[0]
    plan["balance_remaining"] = "250.00"
    plan["balance_remaining_cents"] = 25000
    plan["total_paid"] = "0.00"
    plan["total_paid_cents"] = 0

    result = asyncio.run(post_gateway_event(_paid(), actor="swipesimple_webhook"))
    stored = db["payment_plans"].docs[0]
    assert result["handled"] is True
    assert stored["status"] == "paid_in_full"
    assert stored["balance_remaining"] == 0
    assert stored["balance_remaining_cents"] == 0
    assert type(stored["balance_remaining_cents"]) is int
    assert stored["total_paid"] == 250.0
    assert stored["total_paid_cents"] == 25000


def test_unreadable_balance_string_does_not_post(db):
    plan = db["payment_plans"].docs[0]
    plan["balance_remaining"] = "not-money"
    plan.pop("balance_remaining_cents", None)
    plan["total_paid"] = "0.00"
    plan.pop("total_paid_cents", None)

    result = asyncio.run(post_gateway_event(_paid(), actor="swipesimple_webhook"))
    assert result.status_code == 500
    assert db["payments"].docs == []
    assert db["payment_plans"].docs[0]["balance_remaining"] == "not-money"
    assert db["payment_plans"].docs[0]["total_paid"] == "0.00"


def test_installment_cron_is_registered():
    from dashboard.cron import CRON_REGISTRY

    match = [row for row in CRON_REGISTRY if row.name == "installment_invoices"]
    assert len(match) == 1
    assert match[0].default_enabled is True
    assert os.getenv("SAAS_MULTI_TENANT", "") in {"", "0", "false", "False"}
