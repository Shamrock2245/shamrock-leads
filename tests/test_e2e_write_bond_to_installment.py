"""Write Bond → premium confirmation → one installment webhook.

Offline. Mongo is the in-memory transaction fake used by the payment-plan
tests (``tests._inmem_mongo.matches`` plus the session in
``tests.test_payment_plan_pay_dedupe``). SwipeSimple HTTP, Gmail, Slack,
and BlueBubbles are mocks. ``SWIPESIMPLE_DISPATCH_LIVE`` stays unset, and
any real socket connect fails the test.

Flow on current main:

1. ``POST /api/ar/write-capture`` records the premium on a ``TEST-`` bond
   (the Write Bond screen's money step).
2. ``POST /api/paperwork/confirm-premium`` with a financed balance greater
   than zero calls ``ensure_payment_plan_for_confirmation`` and writes
   exactly one ``payment_plans`` row, plus the confirmation audit rows.
   The signing invoice is the share-invoice step below.
3. ``maybe_issue_share_invoice_for_bond`` (what DocuSeal completion calls
   once the trio is set) stages one down-payment invoice. HTTP is mocked.
   ``SWIPESIMPLE_LIVE`` is on only so the claim is stored; dispatch stays off.
4. One ``POST /api/webhooks/payment`` for installment 1, then the same body
   again. The installment is recorded once and the replay does not move
   the balance.
5. Merged #182/#187 behavior on the same bond: the Gmail receipt poller sees
   installment 1 too, and the payment-agent KPIs still count it once. A
   down-payment charge stamps ``last_payment_*``; its refund webhook records
   a reversal, flags review, and does not re-stamp ``last_payment_*``. The
   refunded down payment drops out of the KPIs.
"""
from __future__ import annotations

import asyncio
import copy
import os
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.routers import payment_plans as pp
from dashboard.routers.agent_analytics import summarize_settled_payments
from dashboard.routers.accounts_receivable import ar_bp
from dashboard.routers.paperwork import paperwork_bp
from dashboard.routers.webhooks import webhooks_bp
from tests._inmem_mongo import matches
from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller
from tests.test_payment_plan_pay_dedupe import TxnClient

BOOKING = "TEST-E2E-INST"
PACKET_ID = "PKT-TEST-E2E-INST"
BOND_CASE_ID = "TEST-BOND-E2E-INST"
ACTOR = "office@example.invalid"
INDEMNITOR = "Casey Sample"
DEFENDANT = "Casey Sample"
PREMIUM = "1500.00"
DOWN = "500.00"
FINANCED = "1000.00"
INSTALLMENTS = 4
INSTALLMENT = "250.00"
PREMIUM_CENTS = 150000
DOWN_CENTS = 50000
FINANCED_CENTS = 100000
INSTALLMENT_CENTS = 25000
INVOICE_ID = "inv_e2e_installment"
PAY_LINK = "https://swipesimple.test.invalid/invoices/inv_e2e_installment/payment"
TXN = "TX-E2E-INST-1"
DOWN_TXN = "TX-E2E-DOWN-1"


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._i = 0

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        rows = self._docs if length is None else self._docs[:length]
        return copy.deepcopy(rows)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        doc = copy.deepcopy(self._docs[self._i])
        self._i += 1
        return doc


class _Result:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched


class Coll:
    """In-memory collection. Unique indexes and ``_id`` raise DuplicateKeyError."""

    def __init__(self):
        self.docs: list[dict] = []
        self.unique: list[tuple[str, ...]] = []

    async def create_index(self, keys, unique=False, name=None, **kwargs):
        fields = tuple(k for k, _ in keys) if isinstance(keys, list) else (keys,)
        if unique and fields not in self.unique:
            self.unique.append(fields)
        return name

    def _reject_duplicate(self, doc, ignore=None):
        explicit = doc.get("_id")
        if explicit is not None and any(
            other is not ignore and other.get("_id") == explicit for other in self.docs
        ):
            raise DuplicateKeyError("dup _id")
        for fields in self.unique:
            if all(doc.get(field) is None for field in fields):
                continue
            key = tuple(doc.get(field) for field in fields)
            for other in self.docs:
                if other is ignore:
                    continue
                if tuple(other.get(field) for field in fields) == key:
                    raise DuplicateKeyError("dup")

    def _apply(self, doc, update, inserting):
        for key, value in (update.get("$inc") or {}).items():
            current = doc.get(key, 0)
            doc[key] = current + value
        for key, value in (update.get("$set") or {}).items():
            doc[key] = copy.deepcopy(value)
        for key, value in (update.get("$push") or {}).items():
            bucket = doc.get(key)
            if not isinstance(bucket, list):
                bucket = []
                doc[key] = bucket
            bucket.append(copy.deepcopy(value))
        if inserting:
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc[key] = copy.deepcopy(value)

    def _new_from_filter(self, flt):
        doc = {}
        for key, value in (flt or {}).items():
            if str(key).startswith("$") or isinstance(value, dict):
                continue
            doc[key] = copy.deepcopy(value)
        return doc

    async def insert_one(self, doc, session=None, **kwargs):
        stored = copy.deepcopy(doc)
        self._reject_duplicate(stored)
        self.docs.append(stored)
        return None

    async def find_one(self, flt=None, projection=None, **kwargs):
        for doc in self.docs:
            if matches(doc, flt or {}):
                return copy.deepcopy(doc)
        return None

    def find(self, flt=None, projection=None, **kwargs):
        return _Cursor([doc for doc in self.docs if matches(doc, flt or {})])

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        for doc in self.docs:
            if matches(doc, flt or {}):
                self._apply(doc, update, inserting=False)
                return _Result(1)
        if not upsert:
            return _Result(0)
        doc = self._new_from_filter(flt)
        self._apply(doc, update, inserting=True)
        self._reject_duplicate(doc)
        self.docs.append(doc)
        return _Result(0)

    async def find_one_and_update(
        self, flt, update, upsert=False, return_document=None, session=None, **kwargs
    ):
        for doc in self.docs:
            if matches(doc, flt or {}):
                before = copy.deepcopy(doc)
                self._apply(doc, update, inserting=False)
                if return_document == ReturnDocument.AFTER:
                    return copy.deepcopy(doc)
                return before
        if not upsert:
            return None
        doc = self._new_from_filter(flt)
        self._apply(doc, update, inserting=True)
        self._reject_duplicate(doc)
        self.docs.append(doc)
        if return_document == ReturnDocument.AFTER:
            return copy.deepcopy(doc)
        return None


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _id_scan():
    return {
        "success": True,
        "extracted": {
            "full_name": INDEMNITOR,
            "first_name": "Casey",
            "last_name": "Sample",
            "dl_state": "FL",
        },
    }


def _case_shell():
    return {
        "booking_number": BOOKING,
        "bond_case_id": BOND_CASE_ID,
        "packet_id": PACKET_ID,
        "bond_amount": 15000,
        "surety_id": "osi",
        "defendant_name": DEFENDANT,
        "indemnitor_name": INDEMNITOR,
        "indemnitor_email": "indemnitor@example.invalid",
        "county": "Lee",
        "state": "FL",
        "is_test": True,
        "test_case": True,
        "id_scan": _id_scan(),
    }


def _cookie():
    token = _sign_token(
        email=ACTOR,
        role="staff",
        agent_name="Office Staff",
        is_admin=False,
    )
    return {COOKIE_NAME: token}


def _installment_paid():
    return {
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": TXN,
        "amount": INSTALLMENT,
        "custom_fields": {
            "booking_number": BOOKING,
            "reference_id": f"{BOOKING}-I1",
            "plan_id": "",
        },
    }


@pytest.fixture
def world(monkeypatch):
    db = DB()
    shell = _case_shell()
    db["active_bonds"].docs.append(copy.deepcopy(shell))
    db["bond_cases"].docs.append(copy.deepcopy(shell))
    db["paperwork_packets"].docs.append(copy.deepcopy(shell))

    def get(name):
        return db[name]

    def get_db():
        return db

    for target in (
        "dashboard.extensions.get_collection",
        "dashboard.services.ar_service.get_collection",
        "dashboard.services.premium_confirmation_service.get_collection",
        "dashboard.services.payment_plan_creation.get_collection",
        "dashboard.services.plan_payment_poster.get_collection",
        "dashboard.services.swipesimple_invoice_service.get_collection",
        "dashboard.routers.payment_plans.get_collection",
        "dashboard.routers.webhooks.get_collection",
    ):
        monkeypatch.setattr(target, get)
    monkeypatch.setattr("dashboard.extensions.get_db", get_db)
    monkeypatch.setattr("dashboard.services.ar_service.get_db", get_db)
    monkeypatch.setattr("dashboard.services.ledger_service.get_db", get_db)

    client = TxnClient(db)

    async def txn():
        return client

    monkeypatch.setattr("dashboard.services.premium_confirmation_service._transaction_client", txn)
    monkeypatch.setattr(pp, "_txn_client", txn)
    monkeypatch.setattr("dashboard.services.payment_log_claims._transaction_client", txn)
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    pp._payment_claim_index_ready = False
    ss._claim_indexes_ready = False
    import dashboard.services.payment_log_claims as payment_log

    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    monkeypatch.setattr(payment_log, "_index_ready", False)
    payment_log._txn_support_cache.clear()

    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_TEST_CASE_INVOICES", "1")
    for key in (
        "SWIPESIMPLE_DISPATCH_LIVE",
        "SWIPESIMPLE_WEBHOOK_SECRET",
        "SWIPESIMPLE_SESSION",
        "SWIPESIMPLE_COOKIE_JAR",
        "SWIPESIMPLE_CSRF_TOKEN",
        "SLACK_WEBHOOK_URL",
        "SLACK_WEBHOOK_LEADS",
        "SLACK_WEBHOOK_ALERTS",
        "SLACK_WEBHOOK_INTAKE",
        "REQUIRE_DASHBOARD_PIN",
        "SAAS_MULTI_TENANT",
    ):
        monkeypatch.delenv(key, raising=False)

    app = FastAPI()
    app.include_router(ar_bp)
    app.include_router(paperwork_bp)
    app.include_router(webhooks_bp)
    http = TestClient(app)
    http.cookies.update(_cookie())

    net = {"calls": []}

    def _blocked(self, address):
        net["calls"].append(address)
        raise AssertionError(f"real network connect blocked: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _blocked)

    sends = {
        "gmail": AsyncMock(side_effect=AssertionError("gmail send")),
        "bb": AsyncMock(side_effect=AssertionError("bluebubbles send")),
        "bb_direct": AsyncMock(side_effect=AssertionError("bluebubbles send")),
        "dispatch": AsyncMock(side_effect=AssertionError("swipesimple dispatch")),
        "slack": AsyncMock(side_effect=AssertionError("slack post")),
    }
    monkeypatch.setattr(
        "dashboard.services.gmail_reader.GmailReaderService.send_email",
        sends["gmail"],
    )
    monkeypatch.setattr("dashboard.services.bb_client.send_message_universal", sends["bb"])
    monkeypatch.setattr("dashboard.services.bb_client.send_imessage", sends["bb_direct"])
    monkeypatch.setattr(ss, "dispatch_invoice", sends["dispatch"])
    monkeypatch.setattr(ss, "_send_dispatch", sends["dispatch"])
    monkeypatch.setattr("dashboard.routers.events.publish_event", AsyncMock())
    # The recorder's staff "needs review" alert goes to Slack. Mocked here.
    review_alerts = Mock(return_value=True)
    monkeypatch.setattr("writers.slack_notifier.SlackNotifier.notify_alert", review_alerts)

    forms = []

    async def _csrf(_cfg):
        return "csrf-test"

    async def _create(*, form_fields, cfg):
        forms.append(dict(form_fields))
        return {"status_code": 302}

    async def _resolve(**_kwargs):
        return INVOICE_ID

    async def _copy(**_kwargs):
        return PAY_LINK

    monkeypatch.setattr(ss, "_fetch_csrf", _csrf)
    monkeypatch.setattr(ss, "_http_create_invoice", _create)
    monkeypatch.setattr(ss, "_resolve_invoice_id_after_create", _resolve)
    monkeypatch.setattr(ss, "_http_copy_link", _copy)
    monkeypatch.setattr(
        ss,
        "load_swipesimple_session_config",
        lambda: {
            "has_session": True,
            "has_cookie_jar": False,
            "base_url": "https://swipesimple.test.invalid",
            "merchant_account_id": "merchant-test",
            "catalog_item_id": "item-test",
            "catalog_item_name": "Bail Bond Premium",
        },
    )

    try:
        yield {
            "db": db,
            "http": http,
            "forms": forms,
            "net": net,
            "sends": sends,
            "review_alerts": review_alerts,
        }
    finally:
        pp._inflight_txns.clear()
        pp._txn_support_cache.clear()


def _plans(db):
    return [doc for doc in db["payment_plans"].docs if doc.get("source") == "premium_confirmation"]


def _audits(db, **wanted):
    rows = []
    for doc in db["audit_events"].docs:
        if all(doc.get(key) == value for key, value in wanted.items()):
            rows.append(doc)
    return rows


def _flow_through_installment(world) -> str:
    """Write Bond → confirm → down-payment invoice → installment 1 (+ replay).

    Returns the plan id. Every amount is a fixture constant above.
    """
    db = world["db"]
    http = world["http"]
    forms = world["forms"]

    assert os.getenv("SWIPESIMPLE_DISPATCH_LIVE") in (None, "")

    written = http.post("/api/ar/write-capture", json={
        "booking_number": BOOKING,
        "premium": PREMIUM,
        "down_payment": DOWN,
        "defendant_name": DEFENDANT,
        "indemnitor_name": INDEMNITOR,
        "indemnitor_email": "indemnitor@example.invalid",
        "source": "write_bond",
    })
    assert written.status_code == 200, written.text
    assert written.json()["ok"] is True
    bond = db["active_bonds"].docs[0]
    assert bond["booking_number"] == BOOKING
    assert bond["is_test"] is True
    assert bond["premium_cents"] == PREMIUM_CENTS
    assert bond["down_payment_cents"] == DOWN_CENTS
    assert _plans(db) == []
    assert forms == []
    assert len(_audits(db, event_type="ar_money_set")) == 1

    confirmed = http.post("/api/paperwork/confirm-premium", json={
        "premium": PREMIUM,
        "down": DOWN,
        "financed": FINANCED,
        "installments": INSTALLMENTS,
        "frequency": "weekly",
        "booking_number": BOOKING,
        "packet_id": PACKET_ID,
        "bond_case_id": BOND_CASE_ID,
        "premium_confirmed_by": "forged-from-body",
    })
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["success"] is True
    assert body["idempotent"] is False
    assert body["premium_confirmed_by"] == ACTOR
    assert body["down_payment_amount"] == DOWN
    assert body["financed_balance"] == FINANCED
    plans = _plans(db)
    assert len(plans) == 1, f"confirm-premium wrote {len(plans)} plans"
    plan = plans[0]
    plan_id = plan["plan_id"]
    assert body["plan_id"] == plan_id
    assert plan["booking_number"] == BOOKING
    assert plan["source"] == "premium_confirmation"
    assert plan["balance_remaining"] == FINANCED
    assert plan["balance_remaining_cents"] == FINANCED_CENTS
    assert plan["total_paid"] == "0.00"
    assert plan["total_paid_cents"] == 0
    assert plan["payments_made"] == 0
    assert len(plan["installments"]) == INSTALLMENTS
    assert sum(row["amount_cents"] for row in plan["installments"]) == FINANCED_CENTS
    assert all(row["amount_cents"] == INSTALLMENT_CENTS for row in plan["installments"])
    # The signing invoice is the share-invoice step after this response.
    assert forms == []
    assert db["swipesimple_invoice_claims"].docs == []
    assert "swipesimple_invoice_id" not in bond
    assert len(_audits(db, action="premium_confirmed")) == 1
    assert len(_audits(db, event_type="payment_plan_created")) == 1
    assert _audits(db, action="premium_confirmed")[0]["actor"] == ACTOR

    issued = asyncio.run(ss.maybe_issue_share_invoice_for_bond(
        BOND_CASE_ID, channel="imessage", dispatch=False, source="e2e_test",
    ))
    assert issued["ok"] is True
    assert issued.get("dispatch") is None
    assert len(forms) == 1
    form = forms[0]
    assert form["invoice[reference_id]"] == BOOKING
    assert form["invoice[amount]"] == str(DOWN_CENTS)
    assert form["invoice[items][][price]"] == str(DOWN_CENTS)
    claims = db["swipesimple_invoice_claims"].docs
    assert len(claims) == 1
    assert claims[0]["status"] == ss.CLAIM_CREATED
    assert claims[0]["invoice_number"] == BOOKING
    for name in ("bond_cases", "active_bonds"):
        row = db[name].docs[0]
        assert row["swipesimple_invoice_id"] == INVOICE_ID
        assert row["swipesimple_invoice_number"] == BOOKING
        assert row["swipesimple_invoice_amount_cents"] == DOWN_CENTS
        assert row["swipesimple_payment_link"] == PAY_LINK
    # The down-payment invoice does not consume the financed balance.
    assert _plans(db)[0]["balance_remaining_cents"] == FINANCED_CENTS
    assert _plans(db)[0]["total_paid_cents"] == 0
    assert db["payments"].docs == []

    payload = _installment_paid()
    payload["custom_fields"]["plan_id"] = plan_id
    paid = http.post("/api/webhooks/payment", json=payload)
    assert paid.status_code == 200, paid.text
    paid_body = paid.json()
    assert paid_body["success"] is True
    assert paid_body["idempotent"] is False
    assert paid_body["plan_id"] == plan_id

    after = _plans(db)[0]
    assert after["balance_remaining_cents"] == FINANCED_CENTS - INSTALLMENT_CENTS
    assert after["total_paid_cents"] == INSTALLMENT_CENTS
    assert _money(after["balance_remaining"]) == Decimal("750.00")
    assert _money(after["total_paid"]) == Decimal("250.00")
    assert after["payments_made"] == 1
    assert after["status"] == "active"
    assert after["installments"][0]["status"] == "paid"
    assert after["installments"][0]["paid_transaction_id"] == TXN
    assert after["installments"][1]["status"] == "scheduled"
    payments = db["payments"].docs
    assert len(payments) == 1
    assert payments[0]["transaction_id"] == TXN
    assert payments[0]["plan_id"] == plan_id
    assert payments[0]["type"] == "installment"
    assert payments[0]["installment_n"] == 1
    assert _money(payments[0]["amount"]) == Decimal(INSTALLMENT)
    assert len(_audits(db, event_type="payment_plan_payment_applied")) == 1
    assert len(db["swipesimple_invoice_claims"].docs) == 1
    assert len(forms) == 1

    replay = http.post("/api/webhooks/payment", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent"] is True
    replayed = _plans(db)[0]
    assert replayed["balance_remaining_cents"] == FINANCED_CENTS - INSTALLMENT_CENTS
    assert replayed["total_paid_cents"] == INSTALLMENT_CENTS
    assert _money(replayed["balance_remaining"]) == Decimal("750.00")
    assert _money(replayed["total_paid"]) == Decimal("250.00")
    assert replayed["payments_made"] == 1
    assert len(db["payments"].docs) == 1
    assert len(_audits(db, event_type="payment_plan_payment_applied")) == 1
    assert len(_plans(db)) == 1
    assert len(db["swipesimple_invoice_claims"].docs) == 1
    return plan_id


def _assert_nothing_sent(world):
    for spy in world["sends"].values():
        spy.assert_not_called()
    assert world["net"]["calls"] == []
    assert os.getenv("SWIPESIMPLE_DISPATCH_LIVE") in (None, "")


def test_write_bond_confirm_creates_one_plan_and_down_invoice_then_one_installment(world):
    _flow_through_installment(world)
    _assert_nothing_sent(world)


_LAST_PAYMENT_FIELDS = (
    "last_payment_amount",
    "last_payment_at",
    "last_payment_status",
    "last_payment_source",
)


def _last_payment(row):
    return {key: row.get(key) for key in _LAST_PAYMENT_FIELDS}


def _payment_agent_kpis(db):
    """The payment-agent KPI query and summary, over the in-memory rows."""
    now = datetime.now(timezone.utc)
    window = {
        "$gte": (now - timedelta(days=30)).isoformat(),
        "$lte": (now + timedelta(minutes=5)).isoformat(),
    }
    flt = {"$or": [{"timestamp": dict(window)}, {"created_at": dict(window)}]}
    rows = [copy.deepcopy(doc) for doc in db["payments"].docs if matches(doc, flt)]
    return summarize_settled_payments(rows)


def test_poller_dupe_counts_once_and_refund_does_not_stamp_last_payment(world):
    db = world["db"]
    http = world["http"]
    plan_id = _flow_through_installment(world)

    # The Gmail receipt poller sees installment 1 as well: once with only the
    # booking on the receipt, once with the plan reference. Neither adds a row.
    poller = SwipeSimpleReceiptPoller(db=db)
    bare = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": INSTALLMENT, "booking_number": BOOKING, "transaction_id": TXN},
        gmail_message_id="gm-test-e2e-1",
        raw_subject="Payment receipt",
    ))
    assert bare["success"] is True
    assert bare["committed"] is False
    assert bare["duplicate"] is True
    referenced = asyncio.run(poller._apply_bond_payment(
        db,
        {
            "amount": INSTALLMENT,
            "booking_number": BOOKING,
            "transaction_id": TXN,
            "reference_id": f"{BOOKING}-I1",
            "plan_id": plan_id,
        },
        gmail_message_id="gm-test-e2e-2",
        raw_subject="Payment receipt",
    ))
    assert referenced["success"] is True
    assert referenced["plan_applied"] is True
    assert referenced["idempotent"] is True
    assert [row["transaction_id"] for row in db["payments"].docs] == [TXN]
    assert _plans(db)[0]["total_paid_cents"] == INSTALLMENT_CENTS
    assert _plans(db)[0]["payments_made"] == 1
    assert len(_audits(db, event_type="payment_plan_payment_applied")) == 1

    kpis = _payment_agent_kpis(db)
    assert kpis["count"] == 1
    assert round(kpis["total"] * 100) == INSTALLMENT_CENTS
    assert kpis["reversed_skipped"] == 0
    assert kpis["amount_conflicts"] == 0

    # An installment never stamps last_payment_* on the bond.
    for name in ("bond_cases", "active_bonds"):
        assert _last_payment(db[name].docs[0]) == dict.fromkeys(_LAST_PAYMENT_FIELDS)

    # The down-payment charge for this booking stamps both bond mirrors.
    down_charge = {
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": DOWN_TXN,
        "amount": DOWN,
        "custom_fields": {"booking_number": BOOKING},
    }
    charged = http.post("/api/webhooks/payment", json=down_charge)
    assert charged.status_code == 200, charged.text
    assert charged.json()["event_kind"] == "charge"
    assert charged.json()["needs_review"] is False
    stamped = {}
    for name in ("bond_cases", "active_bonds"):
        row = db[name].docs[0]
        assert round(row["last_payment_amount"] * 100) == DOWN_CENTS
        assert row["last_payment_status"] == "approved"
        assert row["last_payment_source"] == "swipesimple_webhook"
        assert not row.get("needs_review")
        stamped[name] = _last_payment(row)
    assert _payment_agent_kpis(db)["count"] == 2
    assert round(_payment_agent_kpis(db)["total"] * 100) == DOWN_CENTS + INSTALLMENT_CENTS

    # Its refund records a reversal row and flags review. last_payment_* stays
    # exactly the charge's stamp.
    refund = dict(down_charge, event_type="payment.refunded", status="refunded")
    refunded = http.post("/api/webhooks/payment", json=refund)
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["event_kind"] == "refund"
    assert refunded.json()["needs_review"] is True
    for name in ("bond_cases", "active_bonds"):
        row = db[name].docs[0]
        assert _last_payment(row) == stamped[name]
        assert row["needs_review"] is True
        assert row["needs_review_transaction_id"] == DOWN_TXN
    down_rows = [row for row in db["payments"].docs if row["transaction_id"] == DOWN_TXN]
    assert sorted(row["status"] for row in down_rows) == ["approved", "refunded"]
    world["review_alerts"].assert_called_once()
    assert world["review_alerts"].call_args.kwargs["title"] == "Provider payment needs review"

    # The refund does not touch the financed plan balance.
    plan = _plans(db)[0]
    assert plan["balance_remaining_cents"] == FINANCED_CENTS - INSTALLMENT_CENTS
    assert plan["total_paid_cents"] == INSTALLMENT_CENTS
    assert plan["payments_made"] == 1

    # KPIs: the refunded down payment drops out; installment 1 counts once.
    kpis = _payment_agent_kpis(db)
    assert kpis["count"] == 1
    assert round(kpis["total"] * 100) == INSTALLMENT_CENTS
    assert kpis["reversed_skipped"] == 1
    assert kpis["amount_conflicts"] == 0

    _assert_nothing_sent(world)
