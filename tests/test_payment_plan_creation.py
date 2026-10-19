"""Payment plan creation for a confirmed premium. In-memory Mongo only."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pymongo.errors import DuplicateKeyError

from dashboard.routers import payment_plans as pp
from dashboard.services import payment_plan_creation as plans
from tests._inmem_mongo import matches

TENANT = "shamrock"
CASE = "BC-TEST-1"
CONFIRMATION = "pcf-test-confirmation"
ACTOR = "office@example.invalid"
FINGERPRINT = "150000:50000:100000:4:weekly"
FIXED = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


class Coll:
    def __init__(self):
        self.docs: list[dict] = []
        self.sessions: list = []
        self.index_calls = 0

    async def create_index(self, *args, **kwargs):
        self.index_calls += 1
        raise AssertionError("payment plan creation must not create an index")

    async def insert_one(self, doc, session=None, **kwargs):
        self.sessions.append(session)
        explicit = doc.get("_id")
        if explicit is not None and any(row.get("_id") == explicit for row in self.docs):
            raise DuplicateKeyError("dup _id")
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, flt=None, *args, **kwargs):
        self.sessions.append(kwargs.get("session"))
        for doc in self.docs:
            if matches(doc, flt or {}):
                return copy.deepcopy(doc)
        return None


class DB(dict):
    def __init__(self):
        super().__init__()
        self.touched: list[str] = []

    def __getitem__(self, name):
        self.touched.append(name)
        if name not in self:
            super().__setitem__(name, Coll())
        return super().__getitem__(name)


class TxnSession:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _restore(self, snap):
        for name, docs in snap.items():
            self.client.db[name].docs = copy.deepcopy(docs)

    async def with_transaction(self, callback):
        while True:
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            self.client.attempts += 1
            try:
                return await callback(self)
            except Exception as exc:
                self._restore(snap)
                for name in list(self.client.db):
                    if name not in snap:
                        self.client.db[name].docs = []
                label = getattr(exc, "has_error_label", None)
                if callable(label) and label("TransientTransactionError") and self.client.attempts < 4:
                    continue
                raise


class TxnClient:
    def __init__(self, db, hello):
        self.db = db
        self.admin = _Admin(hello)
        self.attempts = 0
        self.sessions = 0

    def start_session(self):
        self.sessions += 1
        return TxnSession(self)


class _Admin:
    def __init__(self, hello):
        self._hello = hello

    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return self._hello
        raise RuntimeError(name)


class _Transient(Exception):
    def has_error_label(self, label):
        return label == "TransientTransactionError"


def _case():
    return {
        "bond_case_id": CASE,
        "booking_number": "TEST-1001",
        "packet_id": "PKT-TEST-1",
        "defendant_name": "Casey Sample",
        "surety_id": "osi",
        "premium_confirmed_amount": "1500.00",
        "down_payment_amount": "500.00",
        "financed_balance": "1000.00",
        "premium_confirmation_fingerprint": FINGERPRINT,
        "tenant_id": TENANT,
    }


@pytest.fixture
def db(monkeypatch):
    store = DB()
    store["bond_cases"].docs.append(_case())
    store["active_bonds"].docs.append({
        "bond_case_id": CASE,
        "booking_number": "TEST-1001",
        "tenant_id": TENANT,
        "surety_id": "osi",
    })
    pp._txn_support_cache.clear()
    client = TxnClient(store, {"setName": "atlas-rs", "ok": 1})
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: client)
    monkeypatch.setattr(plans, "get_collection", lambda name: store[name])
    monkeypatch.setattr(plans, "_now", lambda: FIXED)
    yield store
    pp._txn_support_cache.clear()


def _call(**overrides):
    payload = dict(
        tenant_id=TENANT,
        bond_case_id=CASE,
        confirmation_id=CONFIRMATION,
        financed=Decimal("1000.00"),
        installments=4,
        frequency="weekly",
        actor=ACTOR,
    )
    payload.update(overrides)
    return plans.ensure_payment_plan_for_confirmation(**payload)


def _fallback_row(**overrides):
    """#197 fallback shape at 386b7964, under the shared plan key."""
    plan_id = plans.plan_document_id(TENANT, CONFIRMATION)
    row = {
        "_id": plan_id,
        "plan_id": plan_id,
        "booking_number": "TEST-1001",
        "packet_id": "PKT-TEST-1",
        "bond_case_id": CASE,
        "defendant_name": "Casey Sample",
        "surety_id": "osi",
        "total_premium": "1500.00",
        "total_premium_cents": 150000,
        "down_payment": "500.00",
        "down_payment_cents": 50000,
        "balance_remaining": "1000.00",
        "balance_remaining_cents": 100000,
        "financed_balance": "1000.00",
        "installment_count": 4,
        "installments": plans._schedule(100000, 4, "weekly", FIXED),
        "installment_amount": "250.00",
        "frequency": "weekly",
        "next_due_date": "2026-10-22",
        "payments_made": 0,
        "total_paid": "0.00",
        "total_paid_cents": 0,
        "status": "active",
        "source": "premium_confirmation",
        "premium_confirmation_fingerprint": FINGERPRINT,
        "delinquent": False,
        "created_at": FIXED.isoformat(),
        "updated_at": FIXED.isoformat(),
        "created_by": ACTOR,
        "tenant_id": TENANT,
    }
    row.update(overrides)
    return row


def test_create_writes_one_plan_schedule_and_audit(db):
    result = asyncio.run(_call())
    assert result["created"] is True
    plan_id = plans.plan_document_id(TENANT, CONFIRMATION)
    assert result["plan_id"] == plan_id
    assert sum(row["amount_cents"] for row in result["schedule"]) == 100000
    assert [row["due_date"] for row in result["schedule"]] == [
        "2026-10-22", "2026-10-29", "2026-11-05", "2026-11-12",
    ]
    assert all(row["status"] == "scheduled" for row in result["schedule"])

    assert len(db["payment_plans"].docs) == 1
    plan = db["payment_plans"].docs[0]
    assert plan["_id"] == plan_id
    assert plan["plan_id"] == plan_id
    assert plan["source"] == "premium_confirmation"
    assert plan["status"] == "active"
    assert plan["booking_number"] == "TEST-1001"
    assert plan["bond_case_id"] == CASE
    assert plan["surety_id"] == "osi"
    assert plan["total_premium"] == "1500.00"
    assert plan["total_premium_cents"] == 150000
    assert plan["down_payment"] == "500.00"
    assert plan["down_payment_cents"] == 50000
    assert plan["balance_remaining"] == "1000.00"
    assert plan["balance_remaining_cents"] == 100000
    assert plan["financed_balance"] == "1000.00"
    assert plan["installment_count"] == 4
    assert plan["installment_amount"] == "250.00"
    assert plan["payments_made"] == 0
    assert plan["total_paid"] == "0.00"
    assert plan["total_paid_cents"] == 0
    assert plan["created_by"] == ACTOR
    assert plan["premium_confirmation_fingerprint"] == FINGERPRINT
    assert plan["tenant_id"] == TENANT

    audits = db["audit_events"].docs
    assert len(audits) == 1
    audit = audits[0]
    assert audit["event_type"] == "payment_plan_created"
    assert audit["action"] == "payment_plan_created"
    assert audit["_id"] == plans.audit_event_id(TENANT, plan_id)
    assert audit["tenant_id"] == TENANT
    assert audit["actor"] == ACTOR
    assert audit["plan_id"] == plan_id
    assert "payments" not in db.touched
    assert db["payment_plans"].index_calls == 0


def test_replay_does_not_create_a_second_plan_or_audit(db):
    first = asyncio.run(_call())
    second = asyncio.run(_call())
    assert second["created"] is False
    assert second["plan_id"] == first["plan_id"]
    assert second["schedule"] == first["schedule"]
    assert len(db["payment_plans"].docs) == 1
    assert len(db["audit_events"].docs) == 1
    assert db["audit_events"].docs[0]["_id"] == plans.audit_event_id(TENANT, first["plan_id"])


def test_conflicting_terms_raise_and_leave_the_first_plan(db):
    asyncio.run(_call())
    with pytest.raises(plans.PlanTermsConflict):
        asyncio.run(_call(financed=Decimal("800.00"), installments=4))
    assert len(db["payment_plans"].docs) == 1
    assert db["payment_plans"].docs[0]["balance_remaining_cents"] == 100000
    assert len(db["audit_events"].docs) == 1

    with pytest.raises(plans.PlanTermsConflict):
        asyncio.run(_call(frequency="monthly"))
    assert len(db["payment_plans"].docs) == 1


def test_preexisting_fallback_row_is_reused(db):
    """A row already stored under the shared key is not inserted again."""
    db["payment_plans"].docs.append(_fallback_row())
    result = asyncio.run(_call())
    assert result["created"] is False
    assert result["plan_id"] == plans.plan_document_id(TENANT, CONFIRMATION)
    assert len(db["payment_plans"].docs) == 1
    assert db["audit_events"].docs == []
    assert sum(row["amount_cents"] for row in result["schedule"]) == 100000


def test_preexisting_confirmation_fallback_with_random_plan_id_is_not_duplicated(db):
    """#197's own writer uses a random plan_id and no ``_id``.

    That row is still the plan for the booking, so this function must not
    insert a second ``premium_confirmation`` document.
    """
    legacy = _fallback_row()
    legacy.pop("_id")
    legacy["plan_id"] = "a1b2c3d4e5f6"
    db["payment_plans"].docs.append(legacy)
    result = asyncio.run(_call())
    assert result["created"] is False
    assert result["plan_id"] == "a1b2c3d4e5f6"
    assert len(db["payment_plans"].docs) == 1
    assert db["payment_plans"].docs[0]["plan_id"] == "a1b2c3d4e5f6"
    assert db["audit_events"].docs == []


def test_preexisting_fallback_with_different_terms_conflicts(db):
    db["payment_plans"].docs.append(_fallback_row(
        balance_remaining="900.00",
        balance_remaining_cents=90000,
        financed_balance="900.00",
        installment_count=3,
        installments=plans._schedule(90000, 3, "weekly", FIXED),
        installment_amount="300.00",
        premium_confirmation_fingerprint="150000:60000:90000:3:weekly",
    ))
    with pytest.raises(plans.PlanTermsConflict):
        asyncio.run(_call())
    assert len(db["payment_plans"].docs) == 1
    assert db["audit_events"].docs == []


def test_cent_remainder_lands_on_the_last_installment(db):
    result = asyncio.run(_call(financed=Decimal("10.00"), installments=3, frequency="biweekly"))
    cents = [row["amount_cents"] for row in result["schedule"]]
    assert cents == [333, 333, 334]
    assert sum(cents) == 1000
    assert [row["amount"] for row in result["schedule"]] == ["3.33", "3.33", "3.34"]
    assert result["schedule"][-1]["due_date"] == "2026-11-26"
    stored = db["payment_plans"].docs[0]["installments"]
    assert [row["amount_cents"] for row in stored] == cents


@pytest.mark.parametrize(
    "kwargs",
    [
        {"financed": Decimal("0.00")},
        {"financed": Decimal("-5.00")},
        {"financed": Decimal("10.001")},
        {"financed": Decimal("1.009")},
        {"financed": 0.1},
        {"installments": 0},
        {"installments": -2},
        {"installments": True},
        {"installments": 1.5},
        {"frequency": "daily"},
        {"frequency": ""},
        {"financed": Decimal("0.02"), "installments": 3},
        {"confirmation_id": ""},
        {"confirmation_id": "   "},
    ],
)
def test_validation_rejects_bad_terms(db, kwargs):
    with pytest.raises(ValueError):
        asyncio.run(_call(**kwargs))
    assert db["payment_plans"].docs == []
    assert db["audit_events"].docs == []


def test_session_joins_the_caller_transaction(db, monkeypatch):
    joined = object()
    monkeypatch.setattr(
        "dashboard.extensions.get_mongo_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not open its own client")),
    )

    result = asyncio.run(_call(session=joined))
    assert result["created"] is True
    assert joined in db["payment_plans"].sessions
    assert joined in db["audit_events"].sessions
    assert len(db["payment_plans"].docs) == 1
    assert len(db["audit_events"].docs) == 1


def test_transactions_unavailable_writes_nothing(db, monkeypatch):
    pp._txn_support_cache.clear()
    monkeypatch.setattr(
        "dashboard.extensions.get_mongo_client",
        lambda: TxnClient(db, {"ismaster": True, "ok": 1}),
    )
    with pytest.raises(plans.TransactionsUnavailable) as raised:
        asyncio.run(_call())
    assert str(raised.value) == "transactions_unavailable"
    assert db["payment_plans"].docs == []
    assert db["audit_events"].docs == []

    pp._txn_support_cache.clear()
    monkeypatch.setattr(
        "dashboard.extensions.get_mongo_client",
        lambda: (_ for _ in ()).throw(RuntimeError("MONGODB_URI not set")),
    )
    with pytest.raises(plans.TransactionsUnavailable):
        asyncio.run(_call())
    assert db["payment_plans"].docs == []
    assert db["audit_events"].docs == []


def test_audit_failure_rolls_back_the_plan(db):
    real = db["audit_events"]

    class Boom(Coll):
        async def insert_one(self, doc, session=None, **kwargs):
            raise RuntimeError("audit down")

    db["audit_events"] = Boom()
    with pytest.raises(RuntimeError, match="audit down"):
        asyncio.run(_call())
    assert db["payment_plans"].docs == []
    assert real.docs == []


def test_transient_retry_leaves_one_plan_and_one_audit(db, monkeypatch):
    state = {"left": 1}
    original = db["audit_events"].insert_one

    async def flaky(doc, session=None, **kwargs):
        if state["left"]:
            state["left"] -= 1
            raise _Transient("please retry")
        await original(doc, session=session, **kwargs)

    db["audit_events"].insert_one = flaky
    result = asyncio.run(_call())
    assert result["created"] is True
    assert len(db["payment_plans"].docs) == 1
    assert len(db["audit_events"].docs) == 1
    assert db["audit_events"].docs[0]["_id"] == plans.audit_event_id(TENANT, result["plan_id"])


def test_blank_tenant_omits_tenant_id(db):
    db["bond_cases"].docs[0].pop("tenant_id")
    result = asyncio.run(_call(tenant_id=""))
    plan = db["payment_plans"].docs[0]
    assert "tenant_id" not in plan
    assert "tenant_id" not in db["audit_events"].docs[0]
    assert db["audit_events"].docs[0]["_id"] == plans.audit_event_id("", result["plan_id"])
    assert result["plan_id"] != plans.plan_document_id(TENANT, CONFIRMATION)


def test_other_tenant_does_not_reuse_the_plan(db):
    first = asyncio.run(_call())
    second = asyncio.run(_call(tenant_id="otherco", bond_case_id="BC-OTHER"))
    assert first["plan_id"] != second["plan_id"]
    assert len(db["payment_plans"].docs) == 2
    assert {row["tenant_id"] for row in db["payment_plans"].docs} == {TENANT, "otherco"}
