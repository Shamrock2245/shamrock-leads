"""
End-to-end: staff test-case Write Bond (PR #137 mode) -> BondCase -> locked
SwipeSimple invoice -> receipt reconciliation -> PAID.

Write Bond order (dashboard/sl-features.js): the screen first saves the
premium through POST /api/ar/write-capture (ar_service.apply_write_bond_money
-> active_bonds premium + premium_cents), then finalizes the packet
(POST /api/paperwork/packet/finalize, here with ``test_case: true``).

Offline only: every SwipeSimple HTTP call is mocked, Mongo is in memory,
DocuSeal is a stub, BlueBubbles / email / Slack / ledger are mocks. No real
invoice, charge, text, or secret.
"""
from __future__ import annotations

import asyncio
import copy
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.services import ar_service
from dashboard.services.swipesimple_invoice_service import (
    SwipeSimpleInvoiceError,
    maybe_issue_share_invoice_for_bond,
    reconcile_payment,
)
from tests._inmem_mongo import FakeCollection, FakeDB
from tests.test_staff_test_case_write_bond import (
    _client,
    _cookie,
    _docuseal_patches,
    _service,
    _stores,
)
from tests.test_swipesimple_invoice_claims import FakeClaims

BOOKING = "TEST-E2E1"
PACKET_ID = "PKT-TEST-E2E1"
PREMIUM = "1500.00"
PREMIUM_CENTS = 150000
INVOICE_ID = "inv_e2e_writebond"
PAY_LINK = "https://swipesimple.com/invoices/inv_e2e_writebond/payment"


class YieldingCollection(FakeCollection):
    """Yields to the loop on every op so asyncio.gather really interleaves."""

    async def find_one(self, *a, **k):
        await asyncio.sleep(0)
        return await super().find_one(*a, **k)

    async def update_one(self, *a, **k):
        await asyncio.sleep(0)
        return await super().update_one(*a, **k)


class YieldingDB(FakeDB):
    def __missing__(self, name):
        col = YieldingCollection()
        self[name] = col
        return col


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")                 # HTTP is mocked
    monkeypatch.setenv("SWIPESIMPLE_TEST_CASE_INVOICES", "1")   # TEST- opt-in
    for key in ("SWIPESIMPLE_DISPATCH_LIVE", "SWIPESIMPLE_SESSION", "SWIPESIMPLE_COOKIE_JAR",
                "SWIPESIMPLE_CSRF_TOKEN", "STAFF_TEST_CASE_REAL_POWER",
                "STAFF_TEST_CASE_SIGNER_EMAIL", "STAFF_TEST_CASE_EMAIL_ALLOWLIST",
                "BOND_AGENT_NAME", "BOND_AGENT_LICENSE"):
        monkeypatch.delenv(key, raising=False)


class Rig:
    def __init__(self):
        self.db = YieldingDB()
        self.claims = FakeClaims()
        self.forms: List[Dict[str, str]] = []
        self.ledger = AsyncMock(return_value="LEDGER-1")
        self.sends = AsyncMock(side_effect=AssertionError("customer send attempted"))

    # ── step 1: Write Bond money capture ───────────────────────────────────
    def write_capture(self, premium: str = PREMIUM, booking: str = BOOKING) -> dict:
        with patch.object(ar_service, "get_collection", self.db.get_collection), \
             patch.object(ar_service, "commit_money_side_effects", new=AsyncMock()), \
             patch.object(ar_service, "bond_detail", new=AsyncMock(return_value={})):
            return asyncio.run(ar_service.apply_write_bond_money({
                "booking_number": booking,
                "premium": premium,
                "down_payment": "0",
                "defendant_name": "Sample Party One",
                "indemnitor_name": "Sample Party Two",
                "indemnitor_phone": "5550100199",
                "source": "write_bond",
            }, "staff@example.invalid"))

    def active(self, booking: str = BOOKING) -> dict:
        return next(d for d in self.db["active_bonds"].docs if d["booking_number"] == booking)

    # ── step 3: invoice (SwipeSimple HTTP mocked) ──────────────────────────
    async def _csrf(self, _cfg):
        return "csrf-test"

    async def _create(self, *, form_fields, cfg):
        await asyncio.sleep(0)
        self.forms.append(dict(form_fields))
        return {"status_code": 302}

    async def _resolve(self, **_kw):
        return INVOICE_ID

    async def _copy(self, **_kw):
        return PAY_LINK

    async def _claims_coll(self):
        return self.claims

    def ss_patches(self):
        cfg = {"has_session": True, "base_url": "https://swipesimple.com"}
        return [
            patch.object(ss, "get_collection", self.db.get_collection),
            patch.object(ss, "_claims_collection", self._claims_coll),
            patch.object(ss, "_fetch_csrf", self._csrf),
            patch.object(ss, "_http_create_invoice", self._create),
            patch.object(ss, "_resolve_invoice_id_after_create", self._resolve),
            patch.object(ss, "_http_copy_link", self._copy),
            patch.object(ss, "load_swipesimple_session_config", lambda: cfg),
            patch.object(ss, "_send_dispatch", self.sends),
            patch("dashboard.services.bb_client.send_message_universal", self.sends),
            patch("dashboard.services.ledger_service.LedgerService.add_entry", self.ledger),
        ]

    def run(self, coro_fn, *a, **k):
        ps = self.ss_patches()
        for p in ps:
            p.start()
        try:
            return asyncio.run(coro_fn(*a, **k))
        finally:
            for p in reversed(ps):
                p.stop()

    def issue(self, bond_id: str = BOOKING):
        return self.run(maybe_issue_share_invoice_for_bond, bond_id,
                        channel="imessage", dispatch=False, source="staff_test_case")

    def reconcile(self, receipt: dict, booking_number=None):
        return self.run(reconcile_payment, booking_number, receipt)


def _finalize(seed_active: List[dict]):
    """Real test-mode finalize (PR #137) with DocuSeal stubbed."""
    captured: Dict[str, Any] = {}
    svc = _service(captured)
    delivery, payment, ensure = AsyncMock(), AsyncMock(), AsyncMock()
    stores, collections = _stores({"active_bonds": copy.deepcopy(seed_active)})
    client = _client()
    client.cookies.update(_cookie("staff"))
    ps = _docuseal_patches(captured, collections, delivery, payment, ensure, svc)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = client.post("/api/paperwork/packet/finalize", json={
            "test_case": True,
            "surety_id": "osi",
            "booking_number": BOOKING,
            "case_number": "TEST-CASE-E2E1",
            "packet_id": PACKET_ID,
            "defendant_name": "Sample Party One",
            "indemnitor_name": "Sample Party Two",
            "bond_amount": 15000,
            "county": "Lee",
            "state": "FL",
        })
    return resp, stores, payment, delivery


def _receipt(**over) -> dict:
    r = {"reference_id": BOOKING, "invoice_id": INVOICE_ID, "amount": PREMIUM,
         "transaction_id": "TX-E2E-1", "status": "approved"}
    r.update(over)
    return {k: v for k, v in r.items() if v is not None}


def _staged_rig() -> Rig:
    rig = Rig()
    assert rig.write_capture()["ok"] is True
    rig.issue()
    assert len(rig.forms) == 1
    return rig


# ─────────────────────────────────────────────────────────────────────────────
# 1. Write Bond (test case) -> BondCase -> exactly one locked invoice
# ─────────────────────────────────────────────────────────────────────────────

def test_test_case_write_bond_to_one_locked_invoice_then_no_op():
    rig = Rig()

    # Write Bond screen step 1: premium -> Accounts Receivable (active_bonds)
    out = rig.write_capture()
    assert out["ok"] is True
    bond = rig.active()
    assert bond["premium_cents"] == PREMIUM_CENTS
    assert bond["is_test"] is True and bond["test_case"] is True

    # Write Bond screen step 2: test-mode finalize accepts the tagged TEST- row
    resp, stores, payment, delivery = _finalize([bond])
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_test"] is True
    packets = stores["paperwork_packets"].inserts
    assert packets and all(p.get("is_test") is True for p in packets)
    assert payment.await_count == 0 and delivery.await_count == 0
    assert "poa_inventory" not in stores

    # BondCase -> invoice (amount from resolve_locked_premium, ref = booking #)
    assert ss.resolve_locked_premium(rig.active()) == Decimal(PREMIUM)
    first = rig.issue()
    assert first["dispatch"] is None
    assert len(rig.forms) == 1
    form = rig.forms[0]
    assert form["invoice[reference_id]"] == BOOKING
    assert form["invoice[amount]"] == str(PREMIUM_CENTS)
    assert form["invoice[items][][price]"] == str(PREMIUM_CENTS)
    assert form["invoice[unadjusted_amount]"] == str(PREMIUM_CENTS)
    staged = rig.active()
    assert staged["swipesimple_invoice_number"] == BOOKING
    assert staged["swipesimple_invoice_id"] == INVOICE_ID
    assert staged["swipesimple_payment_link"] == PAY_LINK
    assert len(rig.claims.docs) == 1
    claim = rig.claims.docs[0]
    assert claim["status"] == ss.CLAIM_CREATED
    assert claim["invoice_number"] == BOOKING

    # Second run: no SwipeSimple create, same claim reused
    second = rig.issue()
    assert second["create"]["idempotent"] is True
    assert second["create"]["payment_link"] == PAY_LINK
    assert len(rig.forms) == 1
    assert len(rig.claims.docs) == 1 and rig.claims.docs[0] is claim
    rig.sends.assert_not_called()


def test_untagged_test_row_is_refused_by_test_finalize():
    """Before this PR write-capture left TEST- rows untagged -> finalize 409."""
    resp, *_ = _finalize([{"booking_number": BOOKING, "premium_cents": PREMIUM_CENTS}])
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "real_bond_refused"


def test_test_case_invoice_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SWIPESIMPLE_TEST_CASE_INVOICES", raising=False)
    rig = Rig()
    rig.write_capture()
    with pytest.raises(SwipeSimpleInvoiceError, match="staff_test_case_invoice_disabled"):
        rig.issue()
    assert rig.forms == [] and rig.claims.docs == []


def test_stale_bond_cases_copy_never_invoiced():
    """bond_cases copy (staff chain) disagreeing with Write Bond -> fail closed."""
    rig = Rig()
    rig.write_capture()
    rig.db["bond_cases"].docs.append({
        "bond_case_id": "TEST-BOND-E2E1", "booking_number": BOOKING,
        "premium": 1000.0, "premium_amount": 1000.0, "is_test": True,
    })
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_mismatch_across_collections"):
        rig.issue("TEST-BOND-E2E1")
    assert rig.forms == [] and rig.claims.docs == []


def test_estimate_copied_into_bond_cases_is_not_laundered():
    """Promote 10% estimate copied into bond_cases (no flag) is still blocked."""
    rig = Rig()
    rig.db["active_bonds"].docs.append({
        "booking_number": BOOKING, "premium": 1500.0, "source": "intake_promotion",
        "premium_is_estimate": True, "is_test": True,
    })
    rig.db["bond_cases"].docs.append({
        "bond_case_id": "TEST-BOND-E2E1", "booking_number": BOOKING,
        "premium": 1500.0, "premium_amount": 1500.0, "source": "inline_packet_finalize_ensure",
    })
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_estimate_unconfirmed"):
        rig.issue("TEST-BOND-E2E1")
    assert rig.forms == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Receipt reconciliation -> PAID only on invoice # + exact amount
# ─────────────────────────────────────────────────────────────────────────────

def test_matching_receipt_flips_paid_once_and_duplicates_are_idempotent():
    rig = _staged_rig()
    out = rig.reconcile(_receipt())
    assert out["ok"] is True and out["idempotent"] is False
    bond = rig.active()
    assert bond["premium_paid"] is True and bond["payment_status"] == "paid"
    assert bond["last_transaction_id"] == "TX-E2E-1"
    assert bond["swipesimple_paid_invoice_number"] == BOOKING
    assert bond["swipesimple_paid_invoice_id"] == INVOICE_ID
    assert rig.ledger.await_count == 1
    entry = rig.ledger.await_args.args[0]
    assert entry["amount"] == 1500.0 and entry["booking_number"] == BOOKING

    # Same receipt again (redelivery / re-poll): no-op
    again = rig.reconcile(_receipt())
    assert again["idempotent"] is True
    assert rig.ledger.await_count == 1

    # A different transaction on an already-PAID bond: refused for review
    with pytest.raises(SwipeSimpleInvoiceError, match="bond_already_paid_different_transaction"):
        rig.reconcile(_receipt(transaction_id="TX-E2E-2"))
    assert rig.ledger.await_count == 1
    assert rig.active()["last_transaction_id"] == "TX-E2E-1"
    assert len(rig.forms) == 1
    rig.sends.assert_not_called()


def test_concurrent_duplicate_receipts_write_one_ledger_entry():
    rig = _staged_rig()

    async def both():
        return await asyncio.gather(
            reconcile_payment(None, _receipt()),
            reconcile_payment(None, _receipt()),
        )

    results = rig.run(both)
    assert sorted(r["idempotent"] for r in results) == [False, True]
    assert rig.ledger.await_count == 1


BAD_RECEIPTS = [
    (dict(amount="1499.99"), "receipt_partial_payment"),
    (dict(amount="750.00"), "receipt_partial_payment"),
    (dict(amount="1500.01"), "receipt_amount_mismatch"),
    (dict(amount=None), "receipt_amount_missing"),
    (dict(amount="1500.005"), "receipt_amount_invalid"),
    (dict(amount="TBD"), "receipt_amount_invalid"),
    (dict(reference_id="TEST-E2E2"), "invoice_not_staged_for_booking"),
    (dict(reference_id=None), "receipt_invoice_number_missing"),
    (dict(invoice_id="inv_someone_else"), "receipt_invoice_id_mismatch"),
    (dict(status="declined"), "receipt_status_not_paid"),
    (dict(status="refunded"), "receipt_status_not_paid"),
    (dict(status=None), "receipt_status_missing"),
    (dict(status=""), "receipt_status_missing"),
    (dict(status="   "), "receipt_status_missing"),
    (dict(transaction_id=None), "receipt_transaction_id_missing"),
]


@pytest.mark.parametrize("over,code", BAD_RECEIPTS)
def test_bad_receipt_never_flips_paid(over, code):
    rig = _staged_rig()
    with pytest.raises(SwipeSimpleInvoiceError, match=code):
        rig.reconcile(_receipt(**over))
    bond = rig.active()
    assert bond.get("premium_paid") is not True
    assert bond.get("payment_status") != "paid"
    assert rig.ledger.await_count == 0


def test_booking_argument_must_equal_receipt_invoice_number():
    rig = _staged_rig()
    with pytest.raises(SwipeSimpleInvoiceError, match="receipt_invoice_number_mismatch"):
        rig.reconcile(_receipt(), booking_number="TEST-OTHER1")
    assert rig.active().get("premium_paid") is not True


def test_receipt_for_bond_without_staged_invoice_never_flips():
    rig = Rig()
    rig.write_capture()          # premium on file, but no invoice staged
    with pytest.raises(SwipeSimpleInvoiceError, match="invoice_not_staged_for_booking"):
        rig.reconcile(_receipt())
    assert rig.active().get("premium_paid") is not True
    assert rig.ledger.await_count == 0


def test_stale_bond_cases_premium_blocks_reconcile():
    rig = _staged_rig()
    rig.db["bond_cases"].docs.append({
        "bond_case_id": "TEST-BOND-E2E1", "booking_number": BOOKING,
        "swipesimple_invoice_number": BOOKING, "premium": 1000.0, "premium_amount": 1000.0,
    })
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_mismatch_across_collections"):
        rig.reconcile(_receipt())
    assert rig.active().get("premium_paid") is not True
    assert rig.ledger.await_count == 0


def test_receipt_without_vendor_id_still_needs_invoice_number_and_amount():
    rig = _staged_rig()
    out = rig.reconcile(_receipt(invoice_id=None))
    assert out["ok"] is True and out["idempotent"] is False
    assert "swipesimple_paid_invoice_id" not in rig.active()


def test_legacy_name_match_service_never_flips_paid():
    from dashboard.services.swipesimple_reconciliation_service import (
        SwipeSimpleReconciliationService,
    )

    db = FakeDB()
    db["active_bonds"].docs.append({"_id": "b1", "defendant_name": "Sample Party One",
                                    "booking_number": BOOKING})
    out = asyncio.run(SwipeSimpleReconciliationService(db).reconcile_payment({
        "transaction_id": "TX-NAME-1", "amount": 1.0, "defendant_name": "Sample Party One",
    }))
    assert out["reconciled"] is True and out["premium_paid_flipped"] is False
    bond = db["active_bonds"].docs[0]
    assert bond.get("premium_paid") is not True
    assert bond["last_payment_amount_unverified"] == 1.0


def test_payment_status_key_alone_counts_as_status():
    rig = _staged_rig()
    out = rig.reconcile(_receipt(status=None, payment_status="paid"))
    assert out["ok"] is True and out["idempotent"] is False
    assert rig.active()["premium_paid"] is True
