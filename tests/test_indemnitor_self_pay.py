"""Indemnitor self-pay: portal invoice link, auto 10% premium, auto-send after
signing, premium_paid, and the staff portal routes.

Offline only. Mongo is an in-memory double, SwipeSimple and BlueBubbles are
mocked; every live switch is unset unless a test sets it on the process.
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.services import bond_booking_lookup as bbl
from dashboard.services import portal_invoice_service as pis
from tests._fake_mongo import FakeCollection

BOOKING = "BK-SELF-2026-0001"
CASE_ID = "BC-SELF-1"
LINK = "https://swipesimple.com/invoices/inv_self_1/payment"
STATIC = "https://swipesimple.com/links/lnk_static_123"

_SWITCHES = (
    "SWIPESIMPLE_LIVE",
    "SWIPESIMPLE_DISPATCH_LIVE",
    "SWIPESIMPLE_SESSION",
    "SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING",
    "PREMIUM_AUTO_CONFIRM",
    "SWIPESIMPLE_BASE_URL",
    "SAAS_MULTI_TENANT",
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _SWITCHES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    pis.reset_rate_limits()
    yield
    pis.reset_rate_limits()


def _confirmed(**extra):
    doc = {
        "bond_case_id": CASE_ID,
        "booking_number": BOOKING,
        "bond_amount": 5000,
        "premium_confirmed_amount": "500.00",
        "premium_confirmed_at": "2026-11-04T12:00:00+00:00",
        "premium_confirmed_by": "office@example.invalid",
    }
    doc.update(extra)
    return doc


def _staged(**extra):
    fields = {
        "swipesimple_payment_link": LINK,
        "swipesimple_invoice_number": BOOKING,
        "swipesimple_invoice_bond_id": CASE_ID,
        "swipesimple_invoice_amount_cents": 50000,
    }
    fields.update(extra)
    return _confirmed(**fields)


def _world(monkeypatch, *, cases=(), actives=()):
    stores = {
        "bond_cases": FakeCollection(list(cases)),
        "active_bonds": FakeCollection(list(actives)),
    }

    def get(name):
        stores.setdefault(name, FakeCollection())
        return stores[name]

    monkeypatch.setattr("dashboard.extensions.get_collection", get)
    return stores


# ─────────────────────────────────────────────────────────────────────────────
# Alias-aware lookup (#3 / portal)
# ─────────────────────────────────────────────────────────────────────────────

def test_lookup_reads_legacy_alias_and_padded_value(monkeypatch):
    _world(monkeypatch, cases=[{"_id": 1, "Booking_Number": BOOKING, "bond_case_id": CASE_ID}],
           actives=[{"_id": 2, "booking_number": f"  {BOOKING} ", "bond_case_id": CASE_ID}])
    case, active = _run(bbl.resolve_bond_pair(BOOKING))
    assert case["_id"] == 1 and active["_id"] == 2


def test_lookup_ignores_stale_legacy_when_canonical_differs(monkeypatch):
    _world(monkeypatch, cases=[{"_id": 1, "booking_number": "OTHER", "Booking_Number": BOOKING}])
    assert _run(bbl.resolve_bond_pair(BOOKING)) == (None, None)


def test_lookup_fails_closed_on_two_documents(monkeypatch):
    _world(monkeypatch, cases=[
        {"_id": 1, "booking_number": BOOKING},
        {"_id": 2, "Booking_Number": BOOKING},
    ])
    with pytest.raises(bbl.BondAmbiguous):
        _run(bbl.resolve_bond_pair(BOOKING))


def test_lookup_fails_closed_on_case_id_mismatch(monkeypatch):
    _world(monkeypatch, cases=[{"_id": 1, "booking_number": BOOKING, "bond_case_id": "A"}],
           actives=[{"_id": 2, "booking_number": BOOKING, "bond_case_id": "B"}])
    with pytest.raises(bbl.BondAmbiguous):
        _run(bbl.resolve_bond_pair(BOOKING))


def test_lookup_blank_booking_reads_nothing(monkeypatch):
    stores = _world(monkeypatch, cases=[{"_id": 1, "booking_number": ""}])
    assert _run(bbl.bonds_for_booking("  ", "bond_cases")) == []
    assert stores["bond_cases"].updates == []


# ─────────────────────────────────────────────────────────────────────────────
# Portal view: confirmed amount only, own verified link only, never static
# ─────────────────────────────────────────────────────────────────────────────

def test_view_without_invoice_shows_call_us_and_no_link(monkeypatch):
    _world(monkeypatch, cases=[_confirmed(_id=1)])
    view = _run(pis.portal_payment_view(BOOKING))
    assert view["payment_link"] is None
    assert view["amount_due"] == "500.00"
    assert view["call_phone"] == "(239) 332-2245"
    assert view["invoice_request_allowed"] is True


def test_view_with_verified_invoice_returns_it(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    view = _run(pis.portal_payment_view(BOOKING))
    assert view["payment_link"] == LINK
    assert view["invoice_request_allowed"] is False


@pytest.mark.parametrize("bad", [
    {"swipesimple_payment_link": STATIC},
    {"swipesimple_invoice_number": "OTHER-BOOKING"},
    {"swipesimple_invoice_bond_id": "BC-SOMEONE-ELSE"},
    {"swipesimple_invoice_amount_cents": 49999},
    {"swipesimple_payment_link": "https://evil.example/invoices/x/payment"},
])
def test_view_never_returns_an_unverified_link(monkeypatch, bad):
    _world(monkeypatch, cases=[_staged(_id=1, **bad)])
    view = _run(pis.portal_payment_view(BOOKING))
    assert view["payment_link"] is None
    assert view["call_phone"] == "(239) 332-2245"


def test_view_unconfirmed_premium_shows_no_amount(monkeypatch):
    _world(monkeypatch, cases=[{"_id": 1, "booking_number": BOOKING, "premium": 1500,
                                "swipesimple_payment_link": STATIC}])
    view = _run(pis.portal_payment_view(BOOKING))
    assert view["amount_due"] is None and view["payment_link"] is None
    assert view["invoice_request_allowed"] is False  # PREMIUM_AUTO_CONFIRM off


def test_view_ambiguous_is_flagged(monkeypatch):
    _world(monkeypatch, cases=[_confirmed(_id=1), _confirmed(_id=2)])
    view = _run(pis.portal_payment_view(BOOKING))
    assert view["error"] == "bond_ambiguous" and view["payment_link"] is None


def test_portal_status_has_no_static_link(monkeypatch):
    from dashboard.services import client_portal_service as cps

    _world(monkeypatch, actives=[{"_id": 1, "booking_number": BOOKING, "premium": 1500,
                                  "defendant_name": "T"}])
    monkeypatch.setattr(cps, "get_collection", lambda n: __import__("dashboard.extensions", fromlist=["x"]).get_collection(n))
    out = _run(cps.get_portal_case_status(BOOKING, "indemnitor"))
    blob = repr(out)
    assert "/links/" not in blob and "lnk_" not in blob
    assert "(239) 332-2245" in blob


def test_portal_html_has_no_static_fallback():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "portal.html").read_text()
    assert "lnk_" not in html and "/links/" not in html
    assert "(239) 332-2245" in html
    assert "/invoice-link" in html


def test_client_portal_service_has_no_static_link_constant():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "services"
           / "client_portal_service.py").read_text()
    assert "lnk_" not in src and "SWIPESIMPLE_PAYMENT_LINK" not in src


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/portal/{token}/invoice-link (§2.2)
# ─────────────────────────────────────────────────────────────────────────────

def _token(role="indemnitor", booking=BOOKING):
    return {"role": role, "booking_number": booking, "defendant_name": "T"}


def _post(monkeypatch, token=None, body=None, *, valid=True, times=1):
    from dashboard.routers import client_portal

    tok = _token() if token is None else token
    monkeypatch.setattr(client_portal, "validate_token", AsyncMock(return_value=tok if valid else None))
    app = FastAPI()
    app.include_router(client_portal.portal_bp)
    client = TestClient(app)
    res = None
    for _ in range(times):
        res = client.post("/api/portal/tok-abc/invoice-link", json=body or {})
    return res


def test_endpoint_rejects_bad_token(monkeypatch):
    res = _post(monkeypatch, valid=False)
    assert res.status_code == 401
    assert res.json() == {"ok": False, "error": "token_invalid_or_expired"}


def test_endpoint_rejects_defendant_role(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    res = _post(monkeypatch, token=_token(role="defendant"))
    assert res.status_code == 403 and res.json()["error"] == "role_not_indemnitor"


def test_endpoint_returns_existing_verified_link_without_create(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    create = AsyncMock(side_effect=AssertionError("must not create"))
    monkeypatch.setattr(ss, "create_locked_invoice", create)
    res = _post(monkeypatch)
    assert res.status_code == 200
    body = res.json()
    assert body["payment_url"] == LINK and body["amount_cents"] == 50000
    assert body["amount"] == "500.00" and body["idempotent"] is True
    assert body["dispatch"]["dispatched"] is False
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("docs,code,status", [
    ([], "bond_not_found", 404),
    ([_confirmed(_id=1), _confirmed(_id=2)], "bond_ambiguous", 409),
    ([{"_id": 1, "booking_number": BOOKING, "premium": 1500}], "premium_unconfirmed", 409),
    ([{"_id": 1, "booking_number": BOOKING, "premium": 1500, "premium_is_estimate": True}],
     "premium_estimate_unconfirmed", 409),
    ([_staged(_id=1, premium_paid=True)], "already_paid", 409),
    ([_staged(_id=1, swipesimple_invoice_amount_cents=12345)], "amount_mismatch", 409),
    ([_staged(_id=1, swipesimple_payment_link=STATIC)], "needs_review", 409),
    ([_confirmed(_id=1)], "invoicing_unavailable", 503),  # SWIPESIMPLE_LIVE off
])
def test_endpoint_error_table(monkeypatch, docs, code, status):
    _world(monkeypatch, cases=docs)
    monkeypatch.setattr(ss, "create_locked_invoice", AsyncMock(side_effect=AssertionError("no create")))
    res = _post(monkeypatch)
    assert res.status_code == status
    body = res.json()
    assert body["ok"] is False and body["error"] == code
    assert "http" not in repr(body)


def test_endpoint_missing_booking(monkeypatch):
    _world(monkeypatch)
    res = _post(monkeypatch, token=_token(booking=""))
    assert res.status_code == 409 and res.json()["error"] == "missing_booking_number"


def test_endpoint_rate_limit(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    res = _post(monkeypatch, times=6)
    assert res.status_code == 429
    assert res.json()["error"] == "rate_limited"
    assert res.headers.get("retry-after") == "60"


def _live_create(monkeypatch, stores, *, result=None, exc=None):
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    calls = []

    async def create(bond_id):
        calls.append(bond_id)
        if exc:
            raise exc
        doc = stores["bond_cases"].docs[0]
        doc.update({
            "swipesimple_payment_link": LINK,
            "swipesimple_invoice_number": BOOKING,
            "swipesimple_invoice_bond_id": CASE_ID,
            "swipesimple_invoice_amount_cents": 50000,
        })
        return result or {"payment_link": LINK, "idempotent": False}

    monkeypatch.setattr(ss, "create_locked_invoice", create)
    monkeypatch.setattr(ss, "live_http_enabled", lambda: True)
    return calls


def test_endpoint_creates_once_then_is_idempotent(monkeypatch):
    stores = _world(monkeypatch, cases=[_confirmed(_id=1)])
    calls = _live_create(monkeypatch, stores)
    first = _post(monkeypatch)
    assert first.status_code == 200 and first.json()["created"] is True
    second = _post(monkeypatch)
    assert second.status_code == 200 and second.json()["idempotent"] is True
    assert calls == [CASE_ID]


def test_endpoint_in_progress_is_202(monkeypatch):
    stores = _world(monkeypatch, cases=[_confirmed(_id=1)])
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setattr(ss, "live_http_enabled", lambda: True)
    monkeypatch.setattr(ss, "create_locked_invoice", AsyncMock(return_value={"in_progress": True}))
    res = _post(monkeypatch)
    assert res.status_code == 202 and res.json()["error"] == "in_progress"
    assert stores["bond_cases"].updates == []


@pytest.mark.parametrize("exc,status,code", [
    (ss.SwipeSimpleInvoiceError("swipesimple_session_not_configured"), 503, "session_expired"),
    (ss.SwipeSimpleInvoiceError("premium_mismatch_vs_bondcase"), 409, "amount_mismatch"),
    (ss.SwipeSimpleInvoiceError("staff_test_case_invoice_disabled"), 409, "staff_test_case_invoice_disabled"),
    (ss.SwipeSimpleInvoiceError("invoice_claim_stale_manual_review"), 409, "needs_review"),
    (RuntimeError("boom"), 503, "invoicing_unavailable"),
])
def test_endpoint_maps_create_errors(monkeypatch, exc, status, code):
    stores = _world(monkeypatch, cases=[_confirmed(_id=1)])
    _live_create(monkeypatch, stores, exc=exc)
    res = _post(monkeypatch)
    assert res.status_code == status and res.json() == {"ok": False, "error": code}


def test_send_copy_is_dry_run_without_dispatch_live(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    dispatch = AsyncMock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(ss, "dispatch_invoice", dispatch)
    res = _post(monkeypatch, body={"send_copy": True})
    assert res.status_code == 200
    assert res.json()["dispatch"] == {"dispatched": False, "dry_run": True, "requested": True}
    dispatch.assert_not_called()


def test_send_copy_uses_one_send_claim_when_live(monkeypatch):
    _world(monkeypatch, cases=[_staged(_id=1)])
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    dispatch = AsyncMock(return_value={"sent": True})
    monkeypatch.setattr(ss, "dispatch_invoice", dispatch)
    res = _post(monkeypatch, body={"send_copy": True, "channel": "imessage"})
    assert res.json()["dispatch"]["dispatched"] is True
    dispatch.assert_awaited_once_with(CASE_ID, "imessage")


def test_auto_confirm_switch_off_never_confirms(monkeypatch):
    stores = _world(monkeypatch, cases=[{"_id": 1, "booking_number": BOOKING, "bond_amount": 5000}])
    auto = AsyncMock(side_effect=AssertionError("switch is off"))
    monkeypatch.setattr(pis, "auto_confirm_premium", auto)
    res = _post(monkeypatch)
    assert res.json()["error"] == "premium_unconfirmed"
    assert stores["bond_cases"].updates == []


def test_auto_confirm_switch_on_is_used_by_endpoint(monkeypatch):
    stores = _world(monkeypatch, cases=[{"_id": 1, "booking_number": BOOKING, "bond_amount": 5000,
                                         "bond_case_id": CASE_ID}])
    monkeypatch.setenv("PREMIUM_AUTO_CONFIRM", "1")

    async def auto(*, booking, bond_case=None, active=None):
        stores["bond_cases"].docs[0].update(_confirmed(premium_confirmed_by="auto"))
        return {"ok": True, "idempotent": False}

    monkeypatch.setattr(pis, "auto_confirm_premium", auto)
    res = _post(monkeypatch)  # SWIPESIMPLE_LIVE off → confirmed, but no invoice
    assert res.status_code == 503 and res.json()["error"] == "invoicing_unavailable"
    assert stores["bond_cases"].docs[0]["premium_confirmed_by"] == "auto"


# ─────────────────────────────────────────────────────────────────────────────
# auto_confirm_premium (PREMIUM_AUTO_CONFIRM, default off)
# ─────────────────────────────────────────────────────────────────────────────

class _Txn:
    def __init__(self, stores):
        self.stores = stores

    def start_session(self):
        stores = self.stores

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def with_transaction(self, cb):
                snap = {n: copy.deepcopy(c.docs) for n, c in stores.items()}
                try:
                    return await cb(self)
                except Exception:
                    for n, docs in snap.items():
                        stores[n].docs = docs
                    raise

        return _S()


def _auto_world(monkeypatch, bond_amount=5000, **case_extra):
    from dashboard.services import premium_confirmation_service as pcs

    case = {"_id": 1, "bond_case_id": CASE_ID, "booking_number": BOOKING,
            "bond_amount": bond_amount, "indemnitor_name": "Casey Sample"}
    case.update(case_extra)
    stores = {
        "bond_cases": FakeCollection([case]),
        "active_bonds": FakeCollection(),
        "paperwork_packets": FakeCollection(),
        "audit_events": FakeCollection(),
    }

    def get(name):
        stores.setdefault(name, FakeCollection())
        return stores[name]

    monkeypatch.setattr(pcs, "get_collection", get)
    monkeypatch.setattr("dashboard.extensions.get_collection", get)

    async def txn():
        return _Txn(stores)

    monkeypatch.setattr(pcs, "_transaction_client", txn)
    monkeypatch.setattr(pcs, "_require_identity", AsyncMock(return_value=None))
    return pcs, stores


def test_auto_confirm_off_by_default(monkeypatch):
    pcs, stores = _auto_world(monkeypatch)
    out = _run(pcs.auto_confirm_premium(booking=BOOKING, bond_case=stores["bond_cases"].docs[0]))
    assert out == {"ok": False, "skipped": "switch_off"}
    assert stores["bond_cases"].updates == [] and stores["audit_events"].docs == []


@pytest.mark.parametrize("bond_amount,premium", [(500, "100.00"), (5000, "500.00"), (25000, "2500.00")])
def test_auto_confirm_is_max_100_or_10pct_paid_in_full(monkeypatch, bond_amount, premium):
    monkeypatch.setenv("PREMIUM_AUTO_CONFIRM", "1")
    pcs, stores = _auto_world(monkeypatch, bond_amount=bond_amount)
    case = copy.deepcopy(stores["bond_cases"].docs[0])
    out = _run(pcs.auto_confirm_premium(booking=BOOKING, bond_case=case))
    assert out["ok"] is True and out["premium_confirmed_amount"] == premium
    saved = stores["bond_cases"].docs[0]
    assert saved["premium_confirmed_by"] == "auto"
    assert saved["premium_confirmed_amount"] == premium
    assert saved["financed_balance"] in ("0.00", 0) or saved.get("financed_balance") == "0.00"
    assert len(stores["audit_events"].docs) == 1
    # Idempotent: a second call writes nothing new.
    again = _run(pcs.auto_confirm_premium(booking=BOOKING, bond_case=copy.deepcopy(saved)))
    assert again["ok"] is True and again["idempotent"] is True
    assert len(stores["audit_events"].docs) == 1


def test_auto_confirm_never_overwrites_staff(monkeypatch):
    monkeypatch.setenv("PREMIUM_AUTO_CONFIRM", "1")
    pcs, stores = _auto_world(monkeypatch, **{k: v for k, v in _confirmed().items()
                                              if k.startswith("premium_confirmed")})
    out = _run(pcs.auto_confirm_premium(booking=BOOKING, bond_case=stores["bond_cases"].docs[0]))
    assert out["ok"] is False and out["skipped"] == "already_confirmed"
    assert stores["bond_cases"].updates == []


def test_auto_confirm_skips_staff_entered_unconfirmed_premium(monkeypatch):
    monkeypatch.setenv("PREMIUM_AUTO_CONFIRM", "1")
    pcs, stores = _auto_world(monkeypatch, premium=1750.0, premium_cents=175000)
    out = _run(pcs.auto_confirm_premium(booking=BOOKING, bond_case=stores["bond_cases"].docs[0]))
    assert out == {"ok": False, "skipped": "staff_premium_unconfirmed"}
    assert stores["bond_cases"].updates == []


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch: needs_review is never re-claimed
# ─────────────────────────────────────────────────────────────────────────────

def test_mark_dispatch_needs_review_moves_failed_claim_only(monkeypatch):
    claims = FakeCollection([
        {"bond_id": "A", "dispatch_status": "failed"},
        {"bond_id": "B", "dispatch_status": "sent", "dispatched_at": "x"},
    ])
    monkeypatch.setattr(ss, "_claims_collection", AsyncMock(return_value=claims))
    monkeypatch.setattr(ss, "_load_bond_by_id", AsyncMock(return_value={"bond_case_id": "A"}))
    assert _run(ss.mark_dispatch_needs_review("A", "BB down!")) is True
    assert claims.docs[0]["dispatch_status"] == "needs_review"
    assert claims.docs[0]["dispatch_error_code"] == "bbdown"
    monkeypatch.setattr(ss, "_load_bond_by_id", AsyncMock(return_value={"bond_case_id": "B"}))
    assert _run(ss.mark_dispatch_needs_review("B", "x")) is False
    assert claims.docs[1]["dispatch_status"] == "sent"


def test_auto_send_switch_is_off_by_default(monkeypatch):
    assert ss.auto_send_after_signing_enabled() is False
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    assert ss.auto_send_after_signing_enabled() is True


# ─────────────────────────────────────────────────────────────────────────────
# DocuSeal completion: auto_send step
# ─────────────────────────────────────────────────────────────────────────────

def _completion_run(share=None, auto=None, preexisting=False):
    from dashboard.services import docuseal_completion as dc

    steps = {}
    if share is not None:
        steps[dc.STEP_SHARE] = share
    if auto is not None:
        steps[dc.STEP_AUTO_SEND] = auto
    doc = {"_id": "p1", "packet_id": "PKT-1",
           dc._C: {"claim_token": "tok", "steps": steps, "preexisting_completion": preexisting}}
    packets = FakeCollection([doc])
    run = dc._Run(copy.deepcopy(doc), get_col=lambda n: packets, source="test",
                  submission_id="S1", event_type="submission.completed", cfg={})
    return dc, run, packets


_SHARED = {"state": "done", "ok": True, "dry_run": False, "bond_id": CASE_ID}


def test_auto_send_off_writes_nothing(monkeypatch):
    dc, run, packets = _completion_run(share=dict(_SHARED))
    monkeypatch.setattr(ss, "dispatch_invoice", AsyncMock(side_effect=AssertionError("off")))
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    _run(run.step_auto_send())
    assert packets.updates == []


def test_auto_send_needs_dispatch_live(monkeypatch):
    dc, run, packets = _completion_run(share=dict(_SHARED))
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    monkeypatch.setattr(ss, "dispatch_invoice", AsyncMock(side_effect=AssertionError("dry")))
    _run(run.step_auto_send())
    assert packets.updates == []


@pytest.mark.parametrize("share", [
    None,
    {"state": "skipped", "reason": "premium_unconfirmed"},
    {"state": "done", "ok": True, "dry_run": True, "bond_id": CASE_ID},
    {"state": "done", "ok": True, "dry_run": False},
])
def test_auto_send_waits_for_a_real_staged_invoice(monkeypatch, share):
    dc, run, packets = _completion_run(share=share)
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    monkeypatch.setattr(ss, "dispatch_invoice", AsyncMock(side_effect=AssertionError("no")))
    _run(run.step_auto_send())
    assert packets.updates == []


def test_auto_send_sends_once(monkeypatch):
    dc, run, packets = _completion_run(share=dict(_SHARED))
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    dispatch = AsyncMock(return_value={"sent": True})
    monkeypatch.setattr(ss, "dispatch_invoice", dispatch)
    _run(run.step_auto_send())
    dispatch.assert_awaited_once_with(CASE_ID, "imessage")
    step = packets.docs[0][dc._C]["steps"][dc.STEP_AUTO_SEND]
    assert step["state"] == "done" and step["dispatched"] is True
    # A rerun (same packet, fresh run) never sends again.
    _dc, rerun, _p = dc, dc._Run(copy.deepcopy(packets.docs[0]), get_col=lambda n: packets,
                                 source="test", submission_id="S1", event_type="x", cfg={}), None
    _run(rerun.step_auto_send())
    assert dispatch.await_count == 1


def test_auto_send_failure_goes_to_needs_review_without_retry(monkeypatch):
    dc, run, packets = _completion_run(share=dict(_SHARED))
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    dispatch = AsyncMock(side_effect=ss.SwipeSimpleInvoiceError("bb_send_failed"))
    review = AsyncMock(return_value=True)
    monkeypatch.setattr(ss, "dispatch_invoice", dispatch)
    monkeypatch.setattr(ss, "mark_dispatch_needs_review", review)
    _run(run.step_auto_send())
    review.assert_awaited_once()
    step = packets.docs[0][dc._C]["steps"][dc.STEP_AUTO_SEND]
    assert step["state"] == "needs_review"
    rerun = dc._Run(copy.deepcopy(packets.docs[0]), get_col=lambda n: packets,
                    source="test", submission_id="S1", event_type="x", cfg={})
    _run(rerun.step_auto_send())
    assert dispatch.await_count == 1


def test_auto_send_skips_preexisting_completion(monkeypatch):
    dc, run, packets = _completion_run(share=dict(_SHARED), preexisting=True)
    monkeypatch.setenv("SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    monkeypatch.setattr(ss, "dispatch_invoice", AsyncMock(side_effect=AssertionError("no")))
    _run(run.step_auto_send())
    assert packets.updates == []


def test_auto_send_is_not_a_required_step():
    from dashboard.services import docuseal_completion as dc

    assert dc.STEP_AUTO_SEND not in dc.REQUIRED_STEPS


# ─────────────────────────────────────────────────────────────────────────────
# Staff portal routes: session required, created_by from the session
# ─────────────────────────────────────────────────────────────────────────────

def _staff_app():
    from dashboard.routers import client_portal

    app = FastAPI()
    app.include_router(client_portal.portal_bp)
    return client_portal, TestClient(app)


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/portal/generate", {"booking_number": BOOKING}),
    ("get", f"/api/portal/tokens/{BOOKING}", None),
    ("post", "/api/portal/revoke", {"token": "x"}),
    ("post", "/api/portal/send-link", {"booking_number": BOOKING}),
])
def test_staff_routes_require_a_session(monkeypatch, method, path, body):
    cp, client = _staff_app()
    monkeypatch.setattr(cp, "generate_portal_token", AsyncMock(side_effect=AssertionError("no")))
    res = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert res.status_code == 401 and res.json() == {"error": "staff_auth_required"}


def test_generate_takes_created_by_from_session(monkeypatch):
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    cp, client = _staff_app()
    gen = AsyncMock(return_value={"success": True, "token": "t"})
    monkeypatch.setattr(cp, "generate_portal_token", gen)
    cookie = _sign_token(email="office@example.invalid", role="staff", agent_name="Office",
                         is_admin=False)
    client.cookies.set(COOKIE_NAME, cookie)
    res = client.post("/api/portal/generate",
                      json={"booking_number": BOOKING, "created_by": "forged"})
    assert res.status_code == 201
    assert gen.await_args.kwargs["created_by"] == "office@example.invalid"


def test_recovery_session_is_refused(monkeypatch):
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token

    cp, client = _staff_app()
    monkeypatch.setattr(cp, "generate_portal_token", AsyncMock(side_effect=AssertionError("no")))
    client.cookies.set(COOKIE_NAME, _sign_token(email="r@example.invalid", role="recovery",
                                                agent_name="R", is_admin=False))
    res = client.post("/api/portal/generate", json={"booking_number": BOOKING})
    assert res.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# _load_bond_by_id: alias-aware, fails closed on ambiguity
# ─────────────────────────────────────────────────────────────────────────────

def _ss_world(monkeypatch, *, cases=(), actives=()):
    stores = {"bond_cases": FakeCollection(list(cases)), "active_bonds": FakeCollection(list(actives))}
    monkeypatch.setattr(ss, "get_collection", lambda n: stores.setdefault(n, FakeCollection()))
    return stores


def test_load_bond_by_legacy_booking_alias(monkeypatch):
    _ss_world(monkeypatch, cases=[{"_id": 7, "Booking_Number": BOOKING}])
    doc = _run(ss._load_bond_by_id(BOOKING))
    assert doc["_id"] == 7 and doc["_collection"] == "bond_cases"


def test_load_bond_by_id_prefers_bond_cases_then_active(monkeypatch):
    _ss_world(monkeypatch, actives=[{"_id": 8, "bond_case_id": CASE_ID}])
    assert _run(ss._load_bond_by_id(CASE_ID))["_collection"] == "active_bonds"


def test_load_bond_by_id_ambiguous_fails_closed(monkeypatch):
    _ss_world(monkeypatch, cases=[{"_id": 1, "booking_number": BOOKING},
                                  {"_id": 2, "booking_number": f" {BOOKING}"}])
    with pytest.raises(ss.SwipeSimpleInvoiceError) as err:
        _run(ss._load_bond_by_id(BOOKING))
    assert ss._error_code(err.value) == "bond_ambiguous"


def test_load_bond_by_id_ignores_stale_legacy(monkeypatch):
    _ss_world(monkeypatch, cases=[{"_id": 1, "booking_number": "NEW", "Booking_Number": BOOKING}])
    assert _run(ss._load_bond_by_id(BOOKING)) is None


# ─────────────────────────────────────────────────────────────────────────────
# premium_paid from recorded payments (bond_cases + active mirror)
# ─────────────────────────────────────────────────────────────────────────────

from tests.test_payment_log_claims import (  # noqa: E402  (shared fixtures)
    _record,
    db,  # noqa: F401
    slack_alerts,  # noqa: F401
)

_PP_TRIO = {
    "premium_confirmed_amount": "500.00",
    "premium_confirmed_at": "2026-11-04T12:00:00+00:00",
    "premium_confirmed_by": "auto",
}


def _mirrored(db, **extra):
    db["bond_cases"].docs.append({"_id": "bc-1", "booking_number": "BK-1",
                                  "bond_case_id": "case-1", **_PP_TRIO, **extra})
    db["active_bonds"].docs.append({"_id": "ab-1", "booking_number": "BK-1",
                                    "bond_case_id": "case-1", **_PP_TRIO, **extra})


def test_premium_paid_set_when_payments_reach_confirmed_premium(db):
    _mirrored(db)
    _run(_record(db, txn="TX-PP-1", amount=200))
    assert db["bond_cases"].docs[0].get("premium_paid") is None
    _run(_record(db, txn="TX-PP-2", amount=300))
    for name in ("bond_cases", "active_bonds"):
        doc = db[name].docs[0]
        assert doc["premium_paid"] is True
        assert doc["premium_paid_source"] == "payments_total"
        assert doc["premium_paid_transaction_id"] == "TX-PP-2"
    audit = [a for a in db["audit_events"].docs if a.get("transaction_id") == "TX-PP-2"][0]
    assert len(audit["premium_paid_change"]) == 2
    assert audit["premium_paid_change"][0]["collected_cents"] == 50000
    # Idempotent: a further charge writes no new premium_paid change.
    _run(_record(db, txn="TX-PP-3", amount=10))
    audit3 = [a for a in db["audit_events"].docs if a.get("transaction_id") == "TX-PP-3"][0]
    assert "premium_paid_change" not in audit3


def test_refund_reopens_premium_paid(db):
    _mirrored(db)
    _run(_record(db, txn="TX-PP-A", amount=500))
    assert db["active_bonds"].docs[0]["premium_paid"] is True
    _run(_record(db, kind="refund", txn="TX-PP-A", amount=500))
    for name in ("bond_cases", "active_bonds"):
        assert db[name].docs[0]["premium_paid"] is False
        assert db[name].docs[0]["premium_paid_reopened_transaction_id"] == "TX-PP-A"
    audits = [a for a in db["audit_events"].docs if a.get("event_kind") == "refund"]
    assert audits[0]["premium_paid_change"][0]["new_premium_paid"] is False
    # Audit history is kept.
    assert any(a.get("event_kind") == "charge" for a in db["audit_events"].docs)


def test_premium_paid_needs_a_confirmed_premium(db):
    db["bond_cases"].docs.append({"_id": "bc-1", "booking_number": "BK-1", "premium": 500})
    _run(_record(db, txn="TX-PP-U", amount=500))
    assert db["bond_cases"].docs[0].get("premium_paid") is None


def test_premium_paid_not_cleared_by_a_charge_when_staff_set_it(db):
    _mirrored(db, premium_paid=True)
    _run(_record(db, txn="TX-PP-S", amount=50))
    assert db["bond_cases"].docs[0]["premium_paid"] is True
    assert db["active_bonds"].docs[0]["premium_paid"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Ops: installment invoice pause / resume plan (read only)
# ─────────────────────────────────────────────────────────────────────────────

def test_installment_pause_script_is_read_only_and_counts():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "installment_invoices_pause.py"
    src = path.read_text()
    for verb in ("update_one", "update_many", "insert_one", "delete_", "replace_one",
                 "find_one_and", "bulk_write", "requests.", "httpx"):
        assert verb not in src
    spec = importlib.util.spec_from_file_location("installment_pause", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plans = [{"installments": [
        {"status": "scheduled", "due_date": "2026-11-01"},
        {"status": "scheduled", "due_date": "2026-12-01"},
        {"status": "staged", "due_date": "2026-10-01", "invoice_key": "k"},
    ]}]
    out = mod.summarize({"installment_invoices": {"enabled": True}}, plans, "2026-11-04")
    assert out == {"enabled": True, "active_plans": 1, "due_now": 1,
                   "scheduled_later": 1, "already_staged_or_closed": 1}
    assert mod.action_for("pause", True)["body"] == {"enabled": False}
    assert mod.action_for("pause", False)["needed"] is False
    assert mod.action_for("resume", False)["body"] == {"enabled": True}
    assert mod.main(["--plan", "pause", "--offline"]) == 0
