"""Stripe test-mode agency billing. No network, no live keys, no charges."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.agency_billing import router
from dashboard.services.agency_billing import (
    catalog,
    reduce_billing_event,
    sign_stripe_payload,
    suspension_block,
)
from dashboard.tenancy.context import bind_job_tenant
from tests.test_tenant_scope import MemoryCollection


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch):
    tenants = MemoryCollection(
        [
            {"tenant_id": "gulf_coast_bail", "legal_name": "Gulf Coast Bail", "billing": {}},
            {"tenant_id": "second_desk", "legal_name": "Second Desk", "billing": {}},
        ]
    )
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_MONTHLY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_SETUP", raising=False)
    monkeypatch.delenv("STRIPE_TRIAL_DAYS", raising=False)
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr("dashboard.extensions._mongo_db", {"tenants": tenants})
    return tenants


def _client(admin=True):
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request, call_next):
        if admin:
            request.state.sl_email = "admin@shamrockbailbonds.biz"
            request.state.sl_role = "god_admin"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _event(event_id, kind, obj):
    return {"id": event_id, "type": kind, "data": {"object": obj}}


def _invoice(**extra):
    body = {
        "id": "in_test_1",
        "amount_paid": 4900,
        "currency": "usd",
        "billing_reason": "subscription_cycle",
        "subscription": "sub_test_1",
        "customer": "cus_test_1",
        "hosted_invoice_url": "https://pay.stripe.com/invoice/test",
        "status": "paid",
        "metadata": {"tenant_id": "gulf_coast_bail"},
        "payment_method_details": {"card": {"brand": "visa", "last4": "4242", "number": "4242424242424242"}},
    }
    body.update(extra)
    return body


def test_catalog_does_not_invent_prices(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_MONTHLY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_SETUP", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_COUNTY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_STATE", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_USAGE", raising=False)
    rows = catalog()
    assert [row["code"] for row in rows] == ["setup", "monthly", "county_addon", "state_addon", "usage"]
    assert all(row["amount_cents"] is None and row["configured"] is False for row in rows)


def test_flag_off_billing_is_404(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    monkeypatch.setattr(
        "dashboard.extensions.get_mongo_client",
        lambda: (_ for _ in ()).throw(AssertionError("mongo touched")),
    )
    client = _client()
    assert client.get("/platform/billing").status_code == 404
    assert client.get("/api/platform/billing").status_code == 404
    assert client.post("/api/webhooks/stripe-billing", content=b"{}").status_code == 404


def test_live_key_never_calls_stripe(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_test_monthly")
    called = []
    monkeypatch.setattr(
        "dashboard.services.agency_billing.post_checkout_session",
        lambda form, key: called.append(key),
    )
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "monthly"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "live_key_rejected"
    assert called == []


def test_checkout_uses_the_test_poster_only(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_test_monthly")
    seen = {}

    def poster(form, key):
        seen["form"] = form
        seen["key"] = key
        return {"id": "cs_test_abc", "url": "https://checkout.stripe.com/c/pay/cs_test_abc"}

    monkeypatch.setattr("dashboard.services.agency_billing.post_checkout_session", poster)
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "monthly", "trial": True},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "trial_not_configured"
    assert seen == {}
    monkeypatch.setenv("STRIPE_TRIAL_DAYS", "14")
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "monthly", "trial": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")
    assert body["session_id"] == "cs_test_abc"
    assert seen["key"] == "sk_test_fixture"
    assert seen["form"]["metadata[tenant_id]"] == "gulf_coast_bail"
    assert seen["form"]["subscription_data[trial_period_days]"] == "14"
    assert "card" not in seen["form"]
    shamrock = _client().post(
        "/api/platform/tenants/shamrock/billing/checkout",
        json={"plan_code": "monthly"},
    )
    assert shamrock.status_code == 400
    assert shamrock.json()["error"] == "internal_not_billed"


def test_webhook_signature_and_mrr(monkeypatch):
    tenants = _install(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fixture")
    client = _client(admin=False)
    payload = json.dumps(
        _event("evt_paid", "invoice.paid", _invoice())
    ).encode()
    missing = client.post("/api/webhooks/stripe-billing", content=payload)
    assert missing.status_code == 400
    assert missing.json()["error"] == "signature_invalid"
    assert tenants.docs[0].get("billing", {}) == {}
    header = sign_stripe_payload(payload, "whsec_test_fixture", int(__import__("time").time()))
    paid = client.post(
        "/api/webhooks/stripe-billing",
        content=payload,
        headers={"Stripe-Signature": header},
    )
    assert paid.status_code == 200
    assert paid.json()["billing_status"] == "active"
    stored = tenants.docs[0]["billing"]
    assert stored["mrr_cents"] == 4900
    assert stored["card_last4"] == "4242"
    assert "4242424242424242" not in json.dumps(stored)
    again = client.post(
        "/api/webhooks/stripe-billing",
        content=payload,
        headers={"Stripe-Signature": header},
    )
    assert again.json()["status"] == "duplicate"
    assert tenants.docs[0]["billing"]["mrr_cents"] == 4900

    other = json.dumps(
        _event(
            "evt_other",
            "invoice.paid",
            _invoice(id="in_test_2", amount_paid=2500, metadata={"tenant_id": "second_desk"}),
        )
    ).encode()
    other_header = sign_stripe_payload(other, "whsec_test_fixture", int(__import__("time").time()))
    assert client.post(
        "/api/webhooks/stripe-billing",
        content=other,
        headers={"Stripe-Signature": other_header},
    ).status_code == 200
    overview = _client().get("/api/platform/billing")
    assert overview.status_code == 200
    assert overview.json()["mrr_cents"] == 7400
    assert "price not set" in _client().get("/platform/billing").text or overview.json()["plans"][0]["configured"] is False


def test_failed_payment_then_suspension_blocks_writes(monkeypatch):
    tenants = _install(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fixture")
    client = _client(admin=False)

    def post(event_id, kind, obj):
        payload = json.dumps(_event(event_id, kind, obj)).encode()
        header = sign_stripe_payload(payload, "whsec_test_fixture", int(__import__("time").time()))
        return client.post(
            "/api/webhooks/stripe-billing",
            content=payload,
            headers={"Stripe-Signature": header},
        )

    first = post("evt_fail_1", "invoice.payment_failed", _invoice(status="open", amount_paid=0))
    assert first.json()["billing_status"] == "past_due"
    assert _run(_past_due_allows()) is None
    second = post("evt_fail_2", "invoice.payment_failed", _invoice(id="in_test_2", status="open", amount_paid=0))
    assert second.json()["billing_status"] == "suspended"
    assert tenants.docs[0]["billing"]["failure_count"] == 2

    async def _blocked_preflight():
        from dashboard.services.write_bond_forward_service import preflight_write_bond_forward

        with bind_job_tenant("gulf_coast_bail"):
            return await preflight_write_bond_forward(bond_case_id="missing")

    preflight = _run(_blocked_preflight())
    assert preflight["state"] == "blocked"
    assert preflight["block_reasons"] == ["tenant_suspended"]

    async def _blocked_text():
        from dashboard.services.bb_client import send_message_universal

        with bind_job_tenant("gulf_coast_bail"):
            return await send_message_universal("2395550100", "hello")

    monkeypatch.setattr(
        "dashboard.services.outreach_queue.enqueue_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("queued while suspended")),
    )
    sent = _run(_blocked_text())
    assert sent["error"] == "tenant_suspended"
    assert sent["queued"] is False

    async def _blocked_packet():
        from dashboard.services.docuseal_service import (
            DocuSealPacketValidationError,
            DocuSealService,
        )

        with bind_job_tenant("gulf_coast_bail"):
            try:
                await DocuSealService.create_submission_for_packet(
                    object(),
                    template_id=1,
                    packet_id="pkt",
                    bond_data={},
                )
            except DocuSealPacketValidationError as exc:
                return str(exc)
        return ""

    assert _run(_blocked_packet()) == "tenant_suspended"


async def _past_due_allows():
    with bind_job_tenant("gulf_coast_bail"):
        return await suspension_block()


def test_comp_is_zero_mrr_and_ignores_later_invoices(monkeypatch):
    tenants = _install(monkeypatch)
    client = _client()
    comped = client.post("/api/platform/tenants/gulf_coast_bail/billing/comp", json={"plan_code": "monthly"})
    assert comped.status_code == 200
    assert comped.json()["status"] == "comped"
    assert comped.json()["mrr_cents"] == 0
    billing, outcome = reduce_billing_event(tenants.docs[0], _event("evt_late", "invoice.paid", _invoice()))
    assert outcome == "ignored"
    assert billing["status"] == "comped"
    stranger = _client(admin=False)
    assert stranger.get("/api/platform/billing").status_code == 403


def test_flag_off_suspension_does_not_block_shamrock(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)

    async def go():
        with bind_job_tenant("gulf_coast_bail"):
            return await suspension_block()

    assert _run(go()) is None
