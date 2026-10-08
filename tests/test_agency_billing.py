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
    audits = MemoryCollection()
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_MONTHLY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_SETUP", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_COUNTY", raising=False)
    monkeypatch.delenv("STRIPE_TRIAL_DAYS", raising=False)
    monkeypatch.delenv("STRIPE_ALLOW_LIVEMODE", raising=False)
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr(
        "dashboard.extensions._mongo_db",
        {"tenants": tenants, "audit_events": audits},
    )
    tenants.audits = audits
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
    assert seen["form"]["subscription_data[metadata][tenant_id]"] == "gulf_coast_bail"
    assert seen["form"]["subscription_data[metadata][plan_code]"] == "monthly"
    assert seen["form"]["subscription_data[metadata][trial]"] == "1"
    assert seen["form"]["subscription_data[trial_period_days]"] == "14"
    assert "customer" not in seen["form"]
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
    assert stored["customer_id"] == "cus_test_1"
    assert stored["subscription_id"] == "sub_test_1"
    assert "4242424242424242" not in json.dumps(stored)
    audits = tenants.audits.docs
    assert len(audits) == 1
    assert audits[0]["actor"] == "stripe_webhook"
    assert audits[0]["tenant_id"] == "gulf_coast_bail"
    assert audits[0]["old_state"]["status"] == "unbilled"
    assert audits[0]["new_state"]["status"] == "active"
    assert audits[0]["new_state"]["mrr_cents"] == 4900
    assert "invoice.paid:evt_paid" == audits[0]["reason"]
    assert audits[0]["event_id"]
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


def test_one_customer_and_addon_is_a_subscription_item(monkeypatch):
    tenants = _install(monkeypatch)
    tenants.docs[0]["billing"] = {
        "status": "active",
        "customer_id": "cus_test_1",
        "subscription_id": "sub_test_base",
        "mrr_cents": 4900,
        "items": [],
        "invoices": [],
        "event_ids": [],
    }
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setenv("STRIPE_PRICE_COUNTY", "price_test_county")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_test_monthly")
    checkout_calls = []
    item_calls = []

    def checkout_poster(form, key):
        checkout_calls.append(form)
        return {"id": "cs_test_should_not", "url": "https://checkout.stripe.com/c/pay/cs_test_should_not"}

    def item_poster(form, key):
        item_calls.append((form, key))
        return {"id": "si_test_county", "object": "subscription_item"}

    monkeypatch.setattr("dashboard.services.agency_billing.post_checkout_session", checkout_poster)
    monkeypatch.setattr("dashboard.services.agency_billing.post_subscription_item", item_poster)
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "county_addon", "quantity": 3},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "subscription_item"
    assert body["subscription_item_id"] == "si_test_county"
    assert checkout_calls == []
    assert item_calls[0][1] == "sk_test_fixture"
    form = item_calls[0][0]
    assert form["subscription"] == "sub_test_base"
    assert form["price"] == "price_test_county"
    assert form["quantity"] == "3"
    assert form["metadata[tenant_id]"] == "gulf_coast_bail"
    assert "mode" not in form
    stored = tenants.docs[0]["billing"]
    assert stored["customer_id"] == "cus_test_1"
    assert stored["subscription_id"] == "sub_test_base"
    assert stored["mrr_cents"] == 4900
    assert stored["items"][0]["subscription_item_id"] == "si_test_county"
    assert stored["items"][0]["quantity"] == 3
    audit = tenants.audits.docs[-1]
    assert audit["action"] == "billing_subscription_item"
    assert audit["actor"] == "stripe_checkout"
    assert audit["new_state"]["items"][0]["plan_code"] == "county_addon"

    tenants.docs[0]["billing"]["status"] = "suspended"
    refused = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "monthly"},
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "billing_not_current"
    assert len(item_calls) == 1


def test_existing_customer_is_reused_on_first_checkout(monkeypatch):
    tenants = _install(monkeypatch)
    tenants.docs[0]["billing"] = {
        "customer_id": "cus_test_existing",
        "subscription_id": "",
        "status": "unbilled",
        "items": [],
        "invoices": [],
        "event_ids": [],
    }
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_test_monthly")
    seen = {}

    def poster(form, key):
        seen["form"] = form
        return {"id": "cs_test_abc", "url": "https://checkout.stripe.com/c/pay/cs_test_abc"}

    monkeypatch.setattr("dashboard.services.agency_billing.post_checkout_session", poster)
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/checkout",
        json={"plan_code": "monthly"},
    )
    assert response.status_code == 201
    assert seen["form"]["customer"] == "cus_test_existing"
    assert seen["form"]["subscription_data[metadata][tenant_id]"] == "gulf_coast_bail"


def test_livemode_events_are_rejected_unless_explicitly_enabled(monkeypatch):
    tenants = _install(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fixture")
    client = _client(admin=False)
    payload = json.dumps(
        {**_event("evt_live", "invoice.paid", _invoice()), "livemode": True}
    ).encode()
    header = sign_stripe_payload(payload, "whsec_test_fixture", int(__import__("time").time()))
    rejected = client.post(
        "/api/webhooks/stripe-billing",
        content=payload,
        headers={"Stripe-Signature": header},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "livemode_rejected"
    assert tenants.docs[0].get("billing", {}) == {}
    assert tenants.audits.docs == []
    monkeypatch.setenv("STRIPE_ALLOW_LIVEMODE", "1")
    allowed = client.post(
        "/api/webhooks/stripe-billing",
        content=payload,
        headers={"Stripe-Signature": header},
    )
    assert allowed.status_code == 200
    assert allowed.json()["billing_status"] == "active"
    monkeypatch.delenv("STRIPE_ALLOW_LIVEMODE", raising=False)


def test_other_subscription_cannot_overwrite_or_cancel(monkeypatch):
    tenants = _install(monkeypatch)
    tenants.docs[0]["billing"] = {
        "provider": "stripe_test",
        "status": "past_due",
        "mrr_cents": 4900,
        "customer_id": "cus_test_1",
        "subscription_id": "sub_test_base",
        "failure_count": 1,
        "plan_code": "monthly",
        "items": [{"plan_code": "monthly", "subscription_item_id": "si_base", "quantity": 1, "price_id": "price_test_monthly"}],
        "invoices": [],
        "event_ids": [],
    }
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fixture")
    client = _client(admin=False)

    def post(event_id, kind, obj, **extra):
        payload = json.dumps({**_event(event_id, kind, obj), **extra}).encode()
        header = sign_stripe_payload(payload, "whsec_test_fixture", int(__import__("time").time()))
        return client.post(
            "/api/webhooks/stripe-billing",
            content=payload,
            headers={"Stripe-Signature": header},
        )

    addon_paid = post(
        "evt_addon_paid",
        "invoice.paid",
        _invoice(
            id="in_addon",
            amount_paid=1500,
            subscription="sub_test_addon",
            customer="cus_test_other",
            metadata={"tenant_id": "gulf_coast_bail"},
        ),
    )
    assert addon_paid.status_code == 200
    assert addon_paid.json()["status"] == "noted"
    stored = tenants.docs[0]["billing"]
    assert stored["status"] == "past_due"
    assert stored["mrr_cents"] == 4900
    assert stored["customer_id"] == "cus_test_1"
    assert stored["subscription_id"] == "sub_test_base"
    deleted = post(
        "evt_addon_deleted",
        "customer.subscription.deleted",
        {"id": "sub_test_addon", "customer": "cus_test_other", "metadata": {"tenant_id": "gulf_coast_bail"}},
    )
    assert deleted.json()["status"] == "noted"
    assert tenants.docs[0]["billing"]["status"] == "past_due"
    assert tenants.docs[0]["billing"]["mrr_cents"] == 4900
    base_deleted = post(
        "evt_base_deleted",
        "customer.subscription.deleted",
        {"id": "sub_test_base", "customer": "cus_test_1", "metadata": {"tenant_id": "gulf_coast_bail"}},
    )
    assert base_deleted.json()["billing_status"] == "canceled"
    assert tenants.docs[0]["billing"]["mrr_cents"] == 0
    assert tenants.docs[0]["billing"]["items"] == []
    cancel_audit = tenants.audits.docs[-1]
    assert cancel_audit["actor"] == "stripe_webhook"
    assert cancel_audit["old_state"]["status"] == "past_due"
    assert cancel_audit["new_state"]["status"] == "canceled"


def test_comp_writes_an_audit_row(monkeypatch):
    tenants = _install(monkeypatch)
    response = _client().post(
        "/api/platform/tenants/gulf_coast_bail/billing/comp",
        json={"plan_code": "monthly"},
    )
    assert response.status_code == 200
    audit = tenants.audits.docs[-1]
    assert audit["actor"] == "admin@shamrockbailbonds.biz"
    assert audit["reason"] == "comp"
    assert audit["old_state"]["status"] == "unbilled"
    assert audit["new_state"]["status"] == "comped"
    assert audit["new_state"]["mrr_cents"] == 0
    assert audit["tenant_id"] == "gulf_coast_bail"
    assert audit["event_id"]


def test_direct_bluebubbles_send_is_blocked_when_suspended(monkeypatch):
    tenants = _install(monkeypatch)
    tenants.docs[0]["billing"] = {
        "status": "suspended",
        "customer_id": "cus_test_1",
        "subscription_id": "sub_test_1",
        "items": [],
        "invoices": [],
        "event_ids": [],
    }
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("suspended send reached BlueBubbles")

    monkeypatch.setattr("dashboard.routers.bb_private_api.httpx.AsyncClient", FakeClient)

    async def blocked():
        from dashboard.routers.bb_private_api import BlueBubblesClient

        client = BlueBubblesClient("http://bb.invalid", "not-a-real-password")
        with bind_job_tenant("gulf_coast_bail"):
            return await client.send_human_like("any;-;+12395550100", "hello", typing_delay=0)

    result = _run(blocked())
    assert result["error"] == "tenant_suspended"
    assert result["success"] is False
    assert calls == []

    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)

    class OpenClient(FakeClient):
        async def request(self, *args, **kwargs):
            calls.append(args[1] if len(args) > 1 else args)
            return _FakeResponse()

    monkeypatch.setattr("dashboard.routers.bb_private_api.httpx.AsyncClient", OpenClient)

    async def allowed():
        from dashboard.routers.bb_private_api import BlueBubblesClient

        client = BlueBubblesClient("http://bb.invalid", "not-a-real-password")
        with bind_job_tenant("gulf_coast_bail"):
            return await client.send_human_like("any;-;+12395550100", "hello", typing_delay=0)

    sent = _run(allowed())
    assert sent.get("success") is True
    assert any("/api/v1/message/text" in str(call) for call in calls)


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"

    def json(self):
        return {"data": {"guid": "msg-1"}, "message": "ok"}


def test_flag_off_suspension_does_not_block_shamrock(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)

    async def go():
        with bind_job_tenant("gulf_coast_bail"):
            return await suspension_block()

    assert _run(go()) is None
