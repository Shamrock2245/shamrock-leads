"""
Atomic per-bond claim tests for SwipeSimple Share Invoice (create + dispatch).

Offline only: in-memory async Mongo double (atomic per operation, yields to the
event loop between operations so asyncio.gather really interleaves) and mocked
SwipeSimple HTTP / BlueBubbles. No network, no Mongo server, no customer sends.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.services.swipesimple_invoice_service import (
    CLAIM_CLAIMED,
    CLAIM_CREATED,
    CLAIM_FAILED,
    CLAIM_NEEDS_REVIEW,
    DISPATCH_FAILED,
    DISPATCH_SENDING,
    DISPATCH_SENT,
    INVOICE_CLAIMS_COLLECTION,
    SwipeSimpleInvoiceError,
    create_locked_invoice,
    dispatch_invoice,
)

BOND_ID = "BC-CLAIM-0001"
BOOKING = "CLAIMBK-778899"
PAY_LINK = "https://example.invalid/pay/claim-link-xyz"
PREMIUM = "250.00"
_MISSING = object()


# ─────────────────────────────────────────────────────────────────────────────
# In-memory async collection (subset of Mongo semantics used by the service)
# ─────────────────────────────────────────────────────────────────────────────

def _get(doc: dict, key: str):
    return doc.get(key, _MISSING)


def _matches(doc: dict, filt: dict) -> bool:
    for key, cond in filt.items():
        if key == "$or":
            if not any(_matches(doc, c) for c in cond):
                return False
            continue
        val = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$in":
                    norm = None if val is _MISSING else val
                    if norm not in arg:
                        return False
                elif op == "$exists":
                    if (val is not _MISSING) != bool(arg):
                        return False
                else:  # pragma: no cover
                    raise NotImplementedError(op)
        elif cond is None:
            if val is not _MISSING and val is not None:
                return False
        elif val is _MISSING or val != cond:
            return False
    return True


class FakeClaims:
    def __init__(self):
        self.docs: List[dict] = []
        self.indexes: List[tuple] = []
        self.unique = []  # (field, sparse)

    async def _yield(self):
        await asyncio.sleep(0)

    async def create_index(self, key, unique=False, sparse=False, name=None, **_kw):
        await self._yield()
        self.indexes.append((key, unique, sparse))
        if unique:
            self.unique.append((key, sparse))
        return name or key

    def _check_unique(self, candidate: dict, ignore: Optional[dict] = None):
        for field, sparse in self.unique:
            v = candidate.get(field, _MISSING)
            if v is _MISSING and sparse:
                continue
            for d in self.docs:
                if d is ignore:
                    continue
                if d.get(field, _MISSING) == v:
                    raise DuplicateKeyError(f"E11000 duplicate key {field}")

    def _apply(self, doc: dict, update: dict, inserting: bool):
        for k, v in (update.get("$set") or {}).items():
            doc[k] = v
        if inserting:
            for k, v in (update.get("$setOnInsert") or {}).items():
                doc[k] = v
        for k, v in (update.get("$inc") or {}).items():
            doc[k] = (doc.get(k) or 0) + v

    def _find(self, filt):
        for d in self.docs:
            if _matches(d, filt):
                return d
        return None

    async def find_one(self, filt, *_a, **_kw):
        await self._yield()
        d = self._find(filt)
        return copy.deepcopy(d) if d else None

    async def find_one_and_update(self, filt, update, upsert=False,
                                  return_document=ReturnDocument.BEFORE, **_kw):
        await self._yield()
        # ---- atomic section (no awaits) ----
        d = self._find(filt)
        if d is not None:
            before = copy.deepcopy(d)
            trial = copy.deepcopy(d)
            self._apply(trial, update, inserting=False)
            self._check_unique(trial, ignore=d)
            self._apply(d, update, inserting=False)
            return before if return_document == ReturnDocument.BEFORE else copy.deepcopy(d)
        if not upsert:
            return None
        new = {k: v for k, v in filt.items()
               if not k.startswith("$") and not (isinstance(v, dict)) and v is not None}
        self._apply(new, update, inserting=True)
        self._check_unique(new)
        self.docs.append(new)
        return None if return_document == ReturnDocument.BEFORE else copy.deepcopy(new)

    async def update_one(self, filt, update, **_kw):
        await self._yield()
        d = self._find(filt)
        if d is not None:
            self._apply(d, update, inserting=False)


class Harness:
    """Real create_locked_invoice / dispatch_invoice; Mongo + HTTP + BB mocked."""

    def __init__(self):
        self.claims = FakeClaims()
        self.bond: Dict[str, Any] = {
            "_id": "oid-claim-1",
            "bond_case_id": BOND_ID,
            "booking_number": BOOKING,
            "premium_amount": PREMIUM,
            "defendant_name": "Quinn Claimtest",
            "indemnitor_phone": "2395550188",
            "indemnitor_email": "quinn@example.invalid",
            "_collection": "bond_cases",
        }
        self.http_creates = 0
        self.http_delay = 0.01
        self.http_side_effects: List[Any] = []
        self.sends = 0
        self.send_results: List[Any] = []

    def collection(self, name):
        assert name == INVOICE_CLAIMS_COLLECTION, f"unexpected collection {name}"
        return self.claims

    async def load(self, bond_id):
        return copy.deepcopy(self.bond) if str(bond_id) in (BOND_ID, BOOKING) else None

    async def share_http(self, *, progress=None, **_kw):
        self.http_creates += 1
        await asyncio.sleep(self.http_delay)
        if self.http_side_effects:
            eff = self.http_side_effects.pop(0)
            if callable(eff):
                return eff(progress)
            if isinstance(eff, BaseException):
                raise eff
        return {"payment_link": PAY_LINK, "invoice_id": "inv-claim-1",
                "amount": float(PREMIUM), "amount_cents": 25000}

    async def persist(self, _bond, *, bond_id, booking_number, payment_link, invoice_id=""):
        self.bond.update({
            "swipesimple_payment_link": payment_link,
            "swipesimple_invoice_id": invoice_id,
            "swipesimple_invoice_number": booking_number,
            "swipesimple_invoice_bond_id": bond_id,
        })

    async def send(self, channel, payload):
        self.sends += 1
        await asyncio.sleep(0.01)
        if self.send_results:
            eff = self.send_results.pop(0)
            if isinstance(eff, BaseException):
                raise eff
            return eff
        return True

    def claim_doc(self) -> Optional[dict]:
        for d in self.claims.docs:
            if d.get("bond_id") == BOND_ID:
                return d
        return None


@pytest.fixture
def h(monkeypatch):
    for key in ("SWIPESIMPLE_DISPATCH_LIVE", "SWIPESIMPLE_SESSION", "SWIPESIMPLE_COOKIE_JAR",
                "SWIPESIMPLE_CSRF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    # HTTP itself is mocked below; LIVE only lets the flow reach the claim.
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setattr(ss, "_claim_indexes_ready", False)
    harness = Harness()
    with patch.object(ss, "get_collection", side_effect=harness.collection), \
         patch.object(ss, "_load_bond_by_id", side_effect=harness.load), \
         patch.object(ss, "_share_invoice_http", side_effect=harness.share_http), \
         patch.object(ss, "_persist_invoice_fields", side_effect=harness.persist), \
         patch.object(ss, "_persist_unresolved_create", new=AsyncMock()), \
         patch.object(ss, "load_swipesimple_session_config", return_value={}), \
         patch.object(ss, "_send_dispatch", side_effect=harness.send), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as bb:
        harness.bb = bb
        yield harness
    bb.assert_not_called()  # real BlueBubbles client is never reached


# ─────────────────────────────────────────────────────────────────────────────
# Create claim
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_create_exactly_one_swipesimple_create(h):
    r1, r2 = await asyncio.gather(create_locked_invoice(BOND_ID), create_locked_invoice(BOND_ID))
    assert h.http_creates == 1
    winners = [r for r in (r1, r2) if not r.get("in_progress")]
    losers = [r for r in (r1, r2) if r.get("in_progress")]
    assert len(winners) == 1 and len(losers) == 1
    assert winners[0]["payment_link"] == PAY_LINK and winners[0]["idempotent"] is False
    loser = losers[0]
    assert loser["ok"] is True and loser["idempotent"] is True
    assert loser["payment_link"] is None and loser["claim_status"] == CLAIM_CLAIMED
    doc = h.claim_doc()
    assert doc["status"] == CLAIM_CREATED and doc["invoice_number"] == BOOKING
    # unique indexes ensured
    assert ("bond_id", True, False) in h.claims.indexes
    assert ("invoice_number", True, True) in h.claims.indexes


@pytest.mark.asyncio
async def test_concurrent_create_different_ids_same_bond_one_create(h):
    """Caller A passes bond_case_id, caller B passes booking # → same canonical claim."""
    await asyncio.gather(create_locked_invoice(BOND_ID), create_locked_invoice(BOOKING))
    assert h.http_creates == 1
    assert len(h.claims.docs) == 1


@pytest.mark.asyncio
async def test_after_create_second_call_is_idempotent_no_create(h):
    await create_locked_invoice(BOND_ID)
    again = await create_locked_invoice(BOND_ID)
    assert h.http_creates == 1
    assert again["idempotent"] is True and again["payment_link"] == PAY_LINK


@pytest.mark.asyncio
async def test_duplicate_key_error_is_lost_race_no_create(h):
    async def boom(*_a, **_kw):
        raise DuplicateKeyError("E11000 duplicate key bond_id")

    h.claims.find_one_and_update = boom  # type: ignore[assignment]
    res = await create_locked_invoice(BOND_ID)
    assert h.http_creates == 0
    assert res["ok"] is True and res["idempotent"] is True
    assert res["in_progress"] is True and res["payment_link"] is None


@pytest.mark.asyncio
async def test_failed_claim_can_be_retried_and_creates_once(h):
    h.http_side_effects = [SwipeSimpleInvoiceError("create_invoice_http_422")]
    with pytest.raises(SwipeSimpleInvoiceError, match="create_invoice_http_422"):
        await create_locked_invoice(BOND_ID)
    doc = h.claim_doc()
    assert doc["status"] == CLAIM_FAILED and doc["error_code"] == "create_invoice_http_422"
    first_claim = doc["claim_id"]

    # two concurrent retries → one re-claim from 'failed', one create
    r1, r2 = await asyncio.gather(create_locked_invoice(BOND_ID), create_locked_invoice(BOND_ID))
    assert h.http_creates == 2  # 1 failed + exactly 1 successful retry
    assert sum(1 for r in (r1, r2) if r.get("payment_link") == PAY_LINK) == 1
    doc = h.claim_doc()
    assert doc["status"] == CLAIM_CREATED and doc["claim_id"] != first_claim
    assert doc["attempts"] == 2


@pytest.mark.asyncio
async def test_failure_after_create_posted_is_needs_review_not_retryable(h):
    def posted_then_fail(progress):
        progress["create_posted"] = True
        raise SwipeSimpleInvoiceError("copy_link_missing_payment_url")

    h.http_side_effects = [posted_then_fail]
    with pytest.raises(SwipeSimpleInvoiceError):
        await create_locked_invoice(BOND_ID)
    assert h.claim_doc()["status"] == CLAIM_NEEDS_REVIEW
    with pytest.raises(SwipeSimpleInvoiceError, match="needs_manual_review"):
        await create_locked_invoice(BOND_ID)
    assert h.http_creates == 1


@pytest.mark.asyncio
async def test_stale_claimed_record_never_triggers_create(h, caplog):
    caplog.set_level(logging.INFO)
    h.claims.docs.append({
        "bond_id": BOND_ID,
        "invoice_number": BOOKING,
        "status": CLAIM_CLAIMED,
        "claim_id": "crashed-worker",
        "claimed_at": datetime.now(timezone.utc) - timedelta(minutes=16),
    })
    with pytest.raises(SwipeSimpleInvoiceError, match="stale_manual_review"):
        await create_locked_invoice(BOND_ID)
    assert h.http_creates == 0
    assert h.claim_doc()["claim_id"] == "crashed-worker"  # untouched
    assert "STALE create claim" in caplog.text
    assert BOOKING not in caplog.text


@pytest.mark.asyncio
async def test_fresh_claimed_record_is_in_progress_no_create(h):
    h.claims.docs.append({
        "bond_id": BOND_ID, "invoice_number": BOOKING, "status": CLAIM_CLAIMED,
        "claim_id": "other-worker", "claimed_at": datetime.now(timezone.utc),
    })
    res = await create_locked_invoice(BOND_ID)
    assert h.http_creates == 0
    assert res["in_progress"] is True and res["payment_link"] is None


@pytest.mark.asyncio
async def test_live_gate_off_takes_no_claim(h, monkeypatch):
    monkeypatch.delenv("SWIPESIMPLE_LIVE", raising=False)
    with pytest.raises(ss.SwipeSimpleLiveDisabled):
        await create_locked_invoice(BOND_ID)
    assert h.claims.docs == [] and h.http_creates == 0


@pytest.mark.asyncio
async def test_index_failure_fails_closed_no_create(h):
    async def broken_index(*_a, **_kw):
        raise RuntimeError("index build failed")

    h.claims.create_index = broken_index  # type: ignore[assignment]
    with pytest.raises(SwipeSimpleInvoiceError, match="invoice_claim_index_unavailable"):
        await create_locked_invoice(BOND_ID)
    assert h.http_creates == 0


# ─────────────────────────────────────────────────────────────────────────────
# Logs: bond_id + flags only
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotent_hit_log_has_no_booking_link_or_amount(h, caplog):
    h.bond.update({
        "swipesimple_payment_link": PAY_LINK,
        "swipesimple_invoice_number": BOOKING,
        "swipesimple_invoice_bond_id": BOND_ID,
        "swipesimple_invoice_id": "inv-existing",
    })
    caplog.set_level(logging.INFO, logger="dashboard.services.swipesimple_invoice_service")
    res = await create_locked_invoice(BOND_ID)
    assert res["idempotent"] is True
    hits = [r.getMessage() for r in caplog.records if "idempotent hit" in r.getMessage()]
    assert hits == [f"[ss_invoice] idempotent hit bond_id={BOND_ID} has_link=True has_invoice_id=True"]
    for needle in (BOOKING, PAY_LINK, PREMIUM, "250.0", "Quinn", "2395550188", "quinn@"):
        assert needle not in caplog.text


@pytest.mark.asyncio
async def test_create_and_dispatch_logs_have_no_pii(h, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="dashboard.services.swipesimple_invoice_service")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    await create_locked_invoice(BOND_ID)
    await dispatch_invoice(BOND_ID, channel="imessage")
    for needle in (BOOKING, PAY_LINK, PREMIUM, "250.0", "Quinn", "2395550188", "quinn@"):
        assert needle not in caplog.text, needle


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch claim (once-only)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def created(h, monkeypatch):
    h.bond.update({
        "swipesimple_payment_link": PAY_LINK,
        "swipesimple_invoice_number": BOOKING,
        "swipesimple_invoice_bond_id": BOND_ID,
    })
    h.claims.docs.append({
        "bond_id": BOND_ID, "invoice_number": BOOKING, "status": CLAIM_CREATED,
        "claim_id": "c1", "claimed_at": datetime.now(timezone.utc),
    })
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    return h


@pytest.mark.asyncio
async def test_dispatch_gate_off_is_dry_run_and_takes_no_claim(created, monkeypatch):
    monkeypatch.delenv("SWIPESIMPLE_DISPATCH_LIVE", raising=False)
    res = await dispatch_invoice(BOND_ID)
    assert res["dry_run"] is True and res["sent"] is False
    assert created.sends == 0
    assert "dispatch_status" not in created.claim_doc()


@pytest.mark.asyncio
async def test_concurrent_dispatch_exactly_one_send(created):
    r1, r2 = await asyncio.gather(dispatch_invoice(BOND_ID), dispatch_invoice(BOND_ID))
    assert created.sends == 1
    assert sorted([bool(r1["sent"]), bool(r2["sent"])]) == [False, True]
    loser = r1 if not r1["sent"] else r2
    assert loser.get("in_progress") is True
    doc = created.claim_doc()
    assert doc["dispatch_status"] == DISPATCH_SENT
    assert doc["dispatch_channel"] == "imessage" and doc["dispatched_at"] is not None


@pytest.mark.asyncio
async def test_already_dispatched_means_no_send(created):
    created.claim_doc().update({
        "dispatch_status": DISPATCH_SENT,
        "dispatched_at": datetime.now(timezone.utc),
        "dispatch_channel": "imessage",
    })
    res = await dispatch_invoice(BOND_ID)
    assert created.sends == 0
    assert res["sent"] is False and res["already_dispatched"] is True


@pytest.mark.asyncio
async def test_failed_dispatch_can_be_retried_once(created):
    created.send_results = [RuntimeError("bb down")]
    with pytest.raises(RuntimeError):
        await dispatch_invoice(BOND_ID)
    doc = created.claim_doc()
    assert doc["dispatch_status"] == DISPATCH_FAILED
    assert doc["dispatch_error_code"] == "RuntimeError"

    r1, r2 = await asyncio.gather(dispatch_invoice(BOND_ID), dispatch_invoice(BOND_ID))
    assert created.sends == 2  # 1 failed + exactly 1 retry send
    assert [bool(r1["sent"]), bool(r2["sent"])].count(True) == 1
    doc = created.claim_doc()
    assert doc["dispatch_status"] == DISPATCH_SENT and doc["dispatch_attempts"] == 2

    third = await dispatch_invoice(BOND_ID)
    assert created.sends == 2 and third["already_dispatched"] is True


@pytest.mark.asyncio
async def test_send_not_accepted_marks_failed(created):
    created.send_results = [False]
    res = await dispatch_invoice(BOND_ID)
    assert res["sent"] is False
    doc = created.claim_doc()
    assert doc["dispatch_status"] == DISPATCH_FAILED
    assert doc["dispatch_error_code"] == "send_not_accepted"


@pytest.mark.asyncio
async def test_stale_sending_record_never_auto_resends(created, caplog):
    caplog.set_level(logging.INFO)
    created.claim_doc().update({
        "dispatch_status": DISPATCH_SENDING,
        "dispatch_claim_id": "crashed",
        "dispatch_claimed_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    })
    with pytest.raises(SwipeSimpleInvoiceError, match="dispatch_claim_stale_manual_review"):
        await dispatch_invoice(BOND_ID)
    assert created.sends == 0
    assert created.claim_doc()["dispatch_claim_id"] == "crashed"
    assert "STALE dispatch claim" in caplog.text


@pytest.mark.asyncio
async def test_legacy_bond_without_claim_doc_dispatches_once(h, monkeypatch):
    """Link persisted before the claim ledger existed → upsert dispatch claim."""
    h.bond.update({"swipesimple_payment_link": PAY_LINK, "swipesimple_invoice_bond_id": BOND_ID})
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    await asyncio.gather(dispatch_invoice(BOND_ID), dispatch_invoice(BOND_ID))
    assert h.sends == 1
    assert h.claim_doc()["dispatch_status"] == DISPATCH_SENT
