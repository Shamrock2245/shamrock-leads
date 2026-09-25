"""
DocuSeal completion → stage-only SwipeSimple Share Invoice (never dispatch).

Covers both completion paths (they do NOT share a handler):
  - POST /api/webhooks/docuseal  submission.completed  (dashboard/routers/webhooks.py)
  - LifecycleAutomations.run_docuseal_poller           (missed-webhook backup)

Everything is mocked: no Mongo, no DocuSeal, no SwipeSimple HTTP, no
BlueBubbles/email, no Slack. HMAC verification is exercised for real (valid
signature built from a throwaway test secret) and is NOT modified.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.routers.webhooks import webhooks_bp
from dashboard.services.docuseal_share_invoice import resolve_share_invoice_bond_id
from dashboard.services.lifecycle_automations import LifecycleAutomations

TEST_HMAC_SECRET = "docuseal-test-secret-not-real"
BOND_ID = "BC-TEST-0001"
BOOKING = "TEST-BK-0001"
SUBMISSION_ID = 987654
PACKET_ID = "pkt-test-0001"

# Synthetic PII that must never appear in our logs.
DEFENDANT = "Zelda Testperson"
PHONE = "239-555-0147"
EMAIL = "zelda.testperson@example.invalid"
PAY_LINK = "https://example.invalid/pay/secret-link-abc"
PREMIUM = "1234.00"


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────

class _UpdateResult:
    matched_count = 1
    modified_count = 1


class _Cursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs

    def limit(self, _n: int) -> "_Cursor":
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs: Optional[List[dict]] = None):
        self.docs = docs or []
        self.updates: List[tuple] = []
        self.inserts: List[dict] = []

    async def insert_one(self, doc):
        self.inserts.append(doc)

    async def update_one(self, filt, update, **_kw):
        self.updates.append((filt, update))
        return _UpdateResult()

    async def find_one(self, _query, *_a, **_kw):
        return copy.deepcopy(self.docs[0]) if self.docs else None

    def find(self, *_a, **_kw):
        return _Cursor([copy.deepcopy(d) for d in self.docs])


class FakeDocuSealWebhookSvc:
    """Webhook side: not configured → no PDF download / Drive."""

    is_configured = False


class FakeDocuSealPollerSvc:
    """Poller side: configured; submission reports completed."""

    is_configured = True

    async def get_submission(self, _sid):
        return {"status": "completed"}

    async def download_combined_pdf(self, _sid):  # pragma: no cover - not reached
        raise AssertionError("drive filing should be skipped in these tests")


def _packet(**overrides) -> dict:
    pkt = {
        "_id": "mongo-oid-test",
        "packet_id": PACKET_ID,
        "bond_case_id": BOND_ID,
        "booking_number": BOOKING,
        "defendant_name": DEFENDANT,
        "indemnitor_phone": PHONE,
        "indemnitor_email": EMAIL,
        "surety_id": "osi",
        "esign_provider": "docuseal",
        "status": "pending_signature",
        "docuseal_status": "pending",
        "docuseal_submission_id": SUBMISSION_ID,
        # already archived → poller skips Drive
        "signed_pdf_drive_url": "https://drive.example.invalid/f/1",
    }
    pkt.update(overrides)
    return pkt


def _completed_body() -> bytes:
    return json.dumps(
        {
            "event_type": "submission.completed",
            "timestamp": "2026-09-25T12:00:00Z",
            "data": {"id": SUBMISSION_ID, "status": "completed"},
        }
    ).encode("utf-8")


def _sig(body: bytes) -> str:
    ts = int(time.time())
    mac = hmac.new(
        TEST_HMAC_SECRET.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256
    ).hexdigest()
    return f"{ts}.{mac}"


class InvoiceStore:
    """
    In-memory BondCase + SwipeSimple HTTP double so the REAL
    create_locked_invoice idempotency logic runs (one invoice per bond_id).
    """

    def __init__(self):
        self.bond: Dict[str, Any] = {
            "bond_case_id": BOND_ID,
            "booking_number": BOOKING,
            "premium_amount": PREMIUM,
            "defendant_name": DEFENDANT,
            "indemnitor_phone": PHONE,
            "indemnitor_email": EMAIL,
            "_collection": "bond_cases",
        }
        self.http_creates = 0
        self.create_results: List[dict] = []

    async def load(self, bond_id):
        if str(bond_id) != BOND_ID:
            return None
        return copy.deepcopy(self.bond)

    async def share_http(self, **_kw):
        self.http_creates += 1
        return {
            "payment_link": PAY_LINK,
            "invoice_id": "inv-test-1",
            "amount": float(PREMIUM),
            "amount_cents": 123400,
            "status_code": 302,
        }

    async def persist(self, _bond, *, bond_id, booking_number, payment_link, invoice_id=""):
        self.bond.update(
            {
                "swipesimple_payment_link": payment_link,
                "swipesimple_invoice_id": invoice_id,
                "swipesimple_invoice_number": booking_number,
                "swipesimple_invoice_bond_id": bond_id,
            }
        )


@pytest.fixture
def env_guard():
    """Test secret for HMAC; no Slack; SwipeSimple live/dispatch forced off."""
    keep = {
        k: os.environ.get(k)
        for k in (
            "DOCUSEAL_WEBHOOK_SECRET",
            "SLACK_WEBHOOK_LEADS",
            "SLACK_WEBHOOK_URL",
            "SWIPESIMPLE_LIVE",
            "SWIPESIMPLE_DISPATCH_LIVE",
            "DEBUG",
        )
    }
    os.environ["DOCUSEAL_WEBHOOK_SECRET"] = TEST_HMAC_SECRET
    for k in ("SLACK_WEBHOOK_LEADS", "SLACK_WEBHOOK_URL", "SWIPESIMPLE_LIVE",
              "SWIPESIMPLE_DISPATCH_LIVE", "DEBUG"):
        os.environ.pop(k, None)
    yield
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def webhook_env(env_guard):
    """Mocks every side effect of the DocuSeal webhook except the share-invoice hook."""
    colls = {
        "audit_events": FakeCollection(),
        "paperwork_packets": FakeCollection([_packet()]),
        "bond_cases": FakeCollection(),
    }
    pay_mock = AsyncMock(return_value={"skipped": True, "delivered": False, "reason": "test"})
    seed_mock = AsyncMock(return_value={"success": True, "reason": "test", "gcal": {}})
    with patch("dashboard.routers.webhooks.get_collection", side_effect=lambda n: colls.setdefault(n, FakeCollection())), \
         patch("dashboard.routers.events.publish_event", new=AsyncMock()), \
         patch("dashboard.services.docuseal_service.DocuSealService", FakeDocuSealWebhookSvc), \
         patch("dashboard.services.packet_payment_link_service.maybe_send_packet_payment_link", new=pay_mock), \
         patch("dashboard.services.bond_court_seed_service.seed_court_calendar_for_bond", new=seed_mock):
        app = FastAPI()
        app.include_router(webhooks_bp)
        yield {"client": TestClient(app), "colls": colls, "pay": pay_mock}


@pytest.fixture
def invoice_store():
    """Real maybe_issue + real create_locked_invoice; HTTP/Mongo/dispatch mocked."""
    store = InvoiceStore()
    dispatch_mock = AsyncMock(side_effect=AssertionError("dispatch_invoice must never be called"))
    real_create = ss.create_locked_invoice

    async def create_spy(bond_id):
        res = await real_create(bond_id)
        store.create_results.append(res)
        return res

    create_mock = AsyncMock(side_effect=create_spy)
    # Atomic claim ledger (swipesimple_invoice_claims) is covered by
    # tests/test_swipesimple_invoice_claims.py; here every sequential call wins
    # the claim and idempotency comes from the persisted link. SWIPESIMPLE_LIVE
    # only lets the flow past the pre-claim live gate — HTTP stays mocked.
    prev_live = os.environ.get("SWIPESIMPLE_LIVE")
    os.environ["SWIPESIMPLE_LIVE"] = "1"
    with patch.object(ss, "_claim_invoice_create", new=AsyncMock(return_value=(True, None, "test-claim"))), \
         patch.object(ss, "_finish_invoice_claim", new=AsyncMock()), \
         patch.object(ss, "_load_bond_by_id", side_effect=store.load), \
         patch.object(ss, "_share_invoice_http", side_effect=store.share_http), \
         patch.object(ss, "_persist_invoice_fields", side_effect=store.persist), \
         patch.object(ss, "load_swipesimple_session_config", return_value={}), \
         patch.object(ss, "create_locked_invoice", new=create_mock), \
         patch.object(ss, "dispatch_invoice", new=dispatch_mock):
        store.create_mock = create_mock
        store.dispatch_mock = dispatch_mock
        try:
            yield store
        finally:
            if prev_live is None:
                os.environ.pop("SWIPESIMPLE_LIVE", None)
            else:
                os.environ["SWIPESIMPLE_LIVE"] = prev_live


def _post_completed(client: TestClient):
    body = _completed_body()
    return client.post(
        "/api/webhooks/docuseal",
        content=body,
        headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)},
    )


def _run_poller(packet: dict):
    db = {"paperwork_packets": FakeCollection([packet])}
    with patch("dashboard.services.docuseal_service.DocuSealService", FakeDocuSealPollerSvc):
        return asyncio.run(
            LifecycleAutomations(db).run_docuseal_poller({"file_to_drive": False})
        )


def _assert_no_pii(caplog):
    text = caplog.text
    for needle in (DEFENDANT, PHONE, EMAIL, PAY_LINK, PREMIUM, "1234.0", "123400"):
        assert needle not in text, f"PII/sensitive value leaked to logs: {needle!r}"


def _share_log_lines(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records if "share_invoice" in r.getMessage()]


# ─────────────────────────────────────────────────────────────────────────────
# Webhook path
# ─────────────────────────────────────────────────────────────────────────────

def test_webhook_duplicate_completed_one_create_zero_dispatch(webhook_env, invoice_store, caplog):
    caplog.set_level(logging.INFO)
    r1 = _post_completed(webhook_env["client"])
    r2 = _post_completed(webhook_env["client"])

    assert r1.status_code == 200 and r1.json()["action"] == "signed_and_filed"
    assert r2.status_code == 200 and r2.json()["action"] == "signed_and_filed"

    assert invoice_store.create_mock.await_count == 2        # called per delivery
    assert invoice_store.http_creates == 1                    # exactly ONE invoice created
    assert [r["idempotent"] for r in invoice_store.create_results] == [False, True]
    invoice_store.dispatch_mock.assert_not_awaited()          # ZERO dispatch

    staged = [l for l in _share_log_lines(caplog) if "staged" in l]
    assert staged == [
        f"[docuseal_webhook] share_invoice staged bond_id={BOND_ID} ok=True idempotent=False",
        f"[docuseal_webhook] share_invoice staged bond_id={BOND_ID} ok=True idempotent=True",
    ]
    _assert_no_pii(caplog)


def test_webhook_calls_share_invoice_with_dispatch_false(webhook_env):
    mock = AsyncMock(return_value={"ok": True, "create": {"idempotent": False}, "dispatch": None})
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        resp = _post_completed(webhook_env["client"])
    assert resp.status_code == 200
    mock.assert_awaited_once_with(
        BOND_ID,
        channel="imessage",
        dispatch=False,
        source="docuseal_submission_completed",
    )
    # existing payment-link soft-fail call left intact
    webhook_env["pay"].assert_awaited_once()


def test_webhook_share_invoice_exception_is_soft_fail_no_pii(webhook_env, caplog):
    caplog.set_level(logging.INFO)
    boom = RuntimeError(f"boom {DEFENDANT} {PHONE} {EMAIL} {PAY_LINK} ${PREMIUM}")
    mock = AsyncMock(side_effect=boom)
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        resp = _post_completed(webhook_env["client"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["action"] == "signed_and_filed"
    mock.assert_awaited_once()
    assert (
        f"[docuseal_webhook] share_invoice stage failed (non-fatal) bond_id={BOND_ID} "
        "err_type=RuntimeError"
    ) in _share_log_lines(caplog)
    _assert_no_pii(caplog)


def test_webhook_missing_bond_id_skips_never_guesses(env_guard, caplog):
    caplog.set_level(logging.INFO)
    colls = {"paperwork_packets": FakeCollection([_packet(bond_case_id=None)])}
    mock = AsyncMock()
    with patch("dashboard.routers.webhooks.get_collection", side_effect=lambda n: colls.setdefault(n, FakeCollection())), \
         patch("dashboard.routers.events.publish_event", new=AsyncMock()), \
         patch("dashboard.services.docuseal_service.DocuSealService", FakeDocuSealWebhookSvc), \
         patch("dashboard.services.packet_payment_link_service.maybe_send_packet_payment_link", new=AsyncMock(return_value={})), \
         patch("dashboard.services.bond_court_seed_service.seed_court_calendar_for_bond", new=AsyncMock(return_value={})), \
         patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        app = FastAPI()
        app.include_router(webhooks_bp)
        resp = _post_completed(TestClient(app))
    assert resp.status_code == 200 and resp.json()["action"] == "signed_and_filed"
    mock.assert_not_awaited()   # no fallback to booking # / packet_id
    assert (
        f"[docuseal_webhook] share_invoice stage skipped reason=missing_bond_id packet={PACKET_ID}"
    ) in _share_log_lines(caplog)
    _assert_no_pii(caplog)


def test_webhook_hmac_still_fail_closed_no_share_invoice(env_guard):
    mock = AsyncMock()
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        app = FastAPI()
        app.include_router(webhooks_bp)
        resp = TestClient(app).post(
            "/api/webhooks/docuseal",
            content=_completed_body(),
            headers={"Content-Type": "application/json", "X-DocuSeal-Signature": "1.deadbeef"},
        )
    assert resp.status_code == 401
    mock.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# Poller path (does not share the webhook handler → wired separately)
# ─────────────────────────────────────────────────────────────────────────────

def test_poller_calls_share_invoice_with_dispatch_false(env_guard):
    mock = AsyncMock(return_value={"ok": True, "create": {"idempotent": False}, "dispatch": None})
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        res = _run_poller(_packet())
    assert res["signed"] == 1 and res["errors"] == 0
    mock.assert_awaited_once_with(
        BOND_ID,
        channel="imessage",
        dispatch=False,
        source="docuseal_poller_completed",
    )


def test_poller_duplicate_runs_one_create_zero_dispatch(env_guard, invoice_store, caplog):
    caplog.set_level(logging.INFO)
    r1 = _run_poller(_packet())
    r2 = _run_poller(_packet())
    assert r1["signed"] == 1 and r2["signed"] == 1
    assert invoice_store.create_mock.await_count == 2
    assert invoice_store.http_creates == 1
    assert [r["idempotent"] for r in invoice_store.create_results] == [False, True]
    invoice_store.dispatch_mock.assert_not_awaited()
    _assert_no_pii(caplog)


def test_poller_share_invoice_exception_is_soft_fail_no_pii(env_guard, caplog):
    caplog.set_level(logging.INFO)
    boom = ValueError(f"boom {DEFENDANT} {PHONE} {EMAIL} {PAY_LINK}")
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=AsyncMock(side_effect=boom)):
        res = _run_poller(_packet())
    # packet still marked signed; exception not counted as a poller error
    assert res["signed"] == 1 and res["errors"] == 0
    assert (
        f"[docuseal-poll] share_invoice stage failed (non-fatal) bond_id={BOND_ID} "
        "err_type=ValueError"
    ) in _share_log_lines(caplog)
    _assert_no_pii(caplog)


def test_poller_missing_bond_id_skips(env_guard, caplog):
    caplog.set_level(logging.INFO)
    mock = AsyncMock()
    with patch.object(ss, "maybe_issue_share_invoice_for_bond", new=mock):
        res = _run_poller(_packet(bond_case_id=""))
    assert res["signed"] == 1
    mock.assert_not_awaited()
    assert (
        f"[docuseal-poll] share_invoice stage skipped reason=missing_bond_id packet={PACKET_ID}"
    ) in _share_log_lines(caplog)


def test_webhook_then_poller_same_bond_one_create_zero_dispatch(webhook_env, invoice_store, caplog):
    caplog.set_level(logging.INFO)
    resp = _post_completed(webhook_env["client"])
    assert resp.status_code == 200
    # Poller picked the packet up before the webhook's status write landed (race).
    res = _run_poller(_packet())
    assert res["signed"] == 1
    assert invoice_store.create_mock.await_count == 2
    assert invoice_store.http_creates == 1
    assert [r["idempotent"] for r in invoice_store.create_results] == [False, True]
    invoice_store.dispatch_mock.assert_not_awaited()
    _assert_no_pii(caplog)


# ─────────────────────────────────────────────────────────────────────────────
# bond_id resolver (never guess)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "packet,expected",
    [
        ({"bond_case_id": "BC-1"}, ("BC-1", "ok")),
        ({"bond_case_id": " BC-1 ", "bond_id": "BC-1"}, ("BC-1", "ok")),
        ({"Bond_Case_ID": "BC-2"}, ("BC-2", "ok")),
        ({"bond_id": 42}, ("42", "ok")),
        ({}, (None, "missing_bond_id")),
        ({"bond_case_id": "", "bond_id": None}, (None, "missing_bond_id")),
        ({"booking_number": "BK-1", "packet_id": "p"}, (None, "missing_bond_id")),
        ({"bond_case_id": "BC-1", "bond_id": "BC-9"}, (None, "ambiguous_bond_id")),
        ({"bond_case_id": {"x": 1}}, (None, "ambiguous_bond_id")),
        (None, (None, "missing_bond_id")),
    ],
)
def test_resolve_share_invoice_bond_id(packet, expected):
    assert resolve_share_invoice_bond_id(packet) == expected
