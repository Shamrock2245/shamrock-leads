"""STOP/TCPA consent gate on the send paths that arrived with main (PR #60 et al.)
plus a static audit that every BlueBubbles send goes through the gate.

Owner decision (final, fail-closed): an opted-out number gets NO text of any
kind — marketing, DocuSeal links, payment links, court / FTA / payment
reminders — until it texts START.  No exemptions, no staff override.

No real texts: BlueBubblesClient._request is mocked, Mongo is in-memory, the
DocuSeal API is a stub.  All phone numbers are fictional 555-01xx numbers.
"""
from __future__ import annotations

import ast
import asyncio
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.services.docuseal_completion as dc
import dashboard.services.packet_payment_link_service as pls
from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.services import bb_client as bbc
from dashboard.services import sms_consent_ledger as ledger
from tests import test_docuseal_completion_handler as tdc
from tests._fake_mongo import FakeCollection
from tests.bb_fake_mongo import FakeDB

ROOT = Path(__file__).resolve().parents[1]
OPTED = "2395550161"
OK = "2395550162"
QUEUE_ID = "65f000000000000000000001"
SWITCH_ENV = "DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK"
CONFIRMED = {
    pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 875.0,
    pls.PREMIUM_CONFIRMED_AT_FIELD: "2026-09-24T15:00:00+00:00",
    pls.PREMIUM_CONFIRMED_BY_FIELD: "staff-test",
}


def _run(coro):
    return asyncio.run(coro)


def _bb():
    client = BlueBubblesClient("http://bb.invalid", "not-a-real-password")
    client._request = AsyncMock(return_value={"success": True, "status_code": 200, "data": {"guid": "SENT"}})
    return client


@pytest.fixture
def world():
    """Ledger / outreach in FakeDB; BlueBubbles transport mocked (no network)."""
    fake = FakeDB()
    client = _bb()
    with patch("dashboard.extensions.get_collection", side_effect=fake.get_collection), \
         patch.object(bbc, "get_bb_client", return_value=client), \
         patch("dashboard.services.outreach_queue.enqueue_message", AsyncMock(return_value=QUEUE_ID)) as enq:
        fake.bb = client
        fake.enqueue = enq
        yield fake


def _opt_out(phone=OPTED):
    _run(ledger.record_consent_event(phone=phone, event="opt_out", keyword="stop",
                                     source_message_guid=f"g-{phone}"))


def _texts_to(client, last10):
    return [c for c in client._request.call_args_list
            if (c.kwargs.get("json_body") or {}).get("chatGuid", "").endswith(last10)
            and c.args[1] == "/api/v1/message/text"]


# ── #60 legacy payment link (switch default OFF) — auto call sites ──────────

def _pkt(phone, **kw):
    d = {"_id": "oid-gate-1", "packet_id": "pkt-gate-1", "booking_number": "GATE-BK-1",
         "premium_amount": 500.0, "indemnitor_phone": phone, "defendant_name": "Gale Gate"}
    d.update(CONFIRMED)
    d.update(kw)
    return d


@contextmanager
def _pay_world(packet):
    colls = {"paperwork_packets": FakeCollection([packet])}

    def get_col(name):
        return colls.setdefault(name, FakeCollection())

    with patch.dict(os.environ, {SWITCH_ENV: "true"}), \
         patch.object(pls, "get_collection", side_effect=get_col), \
         patch("dashboard.services.gmail_reader.GmailReaderService") as gm:
        gm.return_value.is_configured = False
        yield colls


@pytest.mark.parametrize("path", ["packet_finalize", "intake_promote", "docuseal_submission_completed"])
def test_legacy_payment_link_auto_send_blocked_for_opted_out(world, path):
    from dashboard.routers.intake import _promote_auto_payment_link
    from dashboard.routers.paperwork import _finalize_auto_payment_link

    _opt_out()
    pkt = _pkt(OPTED)
    with _pay_world(pkt) as colls:
        if path == "packet_finalize":
            res = _run(_finalize_auto_payment_link(pkt["packet_id"], pkt))
        elif path == "intake_promote":
            bond = {"booking_number": pkt["booking_number"], "defendant_name": "Gale Gate", "bond_amount": 5000.0,
                    "premium": 500.0, "indemnitor_phone": OPTED, "indemnitor_email": "",
                    "paperwork_packet_id": pkt["packet_id"]}
            res = _run(_promote_auto_payment_link(bond_doc=bond, intake_doc={},
                                                  matched_booking=pkt["booking_number"], defendant_name="Gale Gate"))
        else:
            res = _run(pls.maybe_send_packet_payment_link(packet_id=pkt["packet_id"], packet_doc=pkt, source=path))
    assert res["delivered"] is False and res["text_delivered"] is False
    assert res["text_error"] == "recipient_opted_out"
    world.bb._request.assert_not_called()          # BlueBubbles never contacted
    world.enqueue.assert_not_called()              # never queued for retry
    assert colls["paperwork_packets"].docs[0][pls.SEND_ONCE_FIELD]["state"] == "not_delivered_manual_review"


def test_legacy_payment_link_auto_send_still_texts_non_opted_out(world):
    from dashboard.routers.paperwork import _finalize_auto_payment_link

    _opt_out()                                     # someone else opted out
    pkt = _pkt(OK)
    with _pay_world(pkt):
        res = _run(_finalize_auto_payment_link(pkt["packet_id"], pkt))
    assert res["text_delivered"] is True
    assert len(_texts_to(world.bb, OK)) == 1


def test_docuseal_completion_handler_legacy_link_blocked_for_opted_out(world):
    """Full #60 shared completion handler (webhook) with the switch ON and a
    staff-confirmed premium: the legacy payment-link text is gated."""
    _opt_out()
    h = tdc.Harness(tdc._packet(indemnitor_phone=OPTED, **CONFIRMED))
    with h.patched(env={dc.LEGACY_PAYMENT_LINK_ENV: "true"}), ExitStack() as st:
        st.enter_context(patch("dashboard.services.packet_payment_link_service.maybe_send_packet_payment_link",
                               new=tdc._REAL_MAYBE_SEND))
        st.enter_context(patch.object(pls, "get_collection", side_effect=h.get_col))
        gm = st.enter_context(patch("dashboard.services.gmail_reader.GmailReaderService"))
        gm.return_value.is_configured = False
        body = tdc._body()
        TestClient(h.app()).post("/api/webhooks/docuseal", content=body,
                                 headers={"Content-Type": "application/json",
                                          "X-DocuSeal-Signature": tdc._sig(body)})
    step = h.step(dc.STEP_LEGACY)
    assert step["state"] == "done" and step["delivered"] is False
    world.bb._request.assert_not_called()
    world.enqueue.assert_not_called()
    assert h.packet_doc()["payment_link_send_once"]["state"] == "not_delivered_manual_review"


# ── staff "Send payment link" endpoint (explicit staff action) — no override ─

def test_staff_payment_link_endpoint_cannot_override_stop(world):
    from dashboard.routers.paperwork import paperwork_bp

    _opt_out()
    app = FastAPI()
    app.include_router(paperwork_bp)
    colls = {}
    with patch.object(pls, "get_collection", side_effect=lambda n: colls.setdefault(n, FakeCollection())), \
         patch("dashboard.services.gmail_reader.GmailReaderService") as gm:
        gm.return_value.is_configured = False
        r = TestClient(app).post("/api/paperwork/payment/swipesimple-link",
                                 json={"amount": 750, "phone": OPTED, "deliver": True})
    data = r.json()
    assert data["text_delivered"] is False and data["text_error"] == "recipient_opted_out"
    world.bb._request.assert_not_called()


def test_share_invoice_dispatch_text_blocked_for_opted_out(world):
    from dashboard.services import swipesimple_invoice_service as ss

    _opt_out()
    sent = _run(ss._send_dispatch("imessage", {"phone": OPTED, "body": "invoice link"}))
    assert sent is False
    world.bb._request.assert_not_called()


# ── reminders: court / FTA / payment are NOT exempt ──────────────────────────

@pytest.mark.parametrize("purpose", ["court_reminder", "fta_alert", "payment_reminder",
                                     "missed_payment", "checkin", "universal"])
def test_reminders_have_no_exemption(world, purpose):
    _opt_out()
    res = _run(bbc.send_message_universal(OPTED, "Court tomorrow 9am", purpose=purpose))
    assert res["blocked"] is True and res["success"] is False
    world.bb._request.assert_not_called()


def test_court_reminder_service_path_blocked(world):
    """court_reminder_service / fta_alert_service call send_message_universal
    with no purpose — still blocked (the gate never keys off purpose)."""
    from dashboard.services.fta_alert_service import FTAAlertService

    _opt_out()
    svc = FTAAlertService.__new__(FTAAlertService)
    res = _run(svc._send_bb(OPTED, "FTA notice"))
    assert res["success"] is False and res.get("blocked") is True
    world.bb._request.assert_not_called()


# ── DocuSeal staff deliver: 409 only for the opted-out signer ────────────────

_DELIVER_PACKET = {
    "packet_id": "PKT-GATE-DELIVER", "defendant_name": "Dee Fendant", "intake_id": "INT-G",
    "status": "pending_signature", "docuseal_status": "sent",
    "docuseal_submitters": [
        {"role": "indemnitor", "sign_url": "https://sign.shamrockbailbonds.biz/s/ind", "phone": OPTED},
        {"role": "Defendant", "sign_url": "https://sign.shamrockbailbonds.biz/s/def", "phone": OK},
    ],
}


def _deliver(world, role):
    from dashboard.routers import paperwork as pw

    app = FastAPI()
    app.include_router(pw.paperwork_bp)
    with patch.object(pw, "_require_control_auth", return_value=None), \
         patch.object(pw, "_load_packet", AsyncMock(return_value=dict(_DELIVER_PACKET))), \
         patch.object(pw, "get_bb_client", return_value=world.bb), \
         patch.object(pw, "get_collection", return_value=AsyncMock()):
        return TestClient(app).post("/api/paperwork/PKT-GATE-DELIVER/deliver", json={"role": role})


def test_staff_deliver_skips_only_opted_out_signer(world):
    _opt_out()
    blocked = _deliver(world, "indemnitor")
    assert blocked.status_code == 409 and blocked.json()["reason"] == "recipient_opted_out"
    ok = _deliver(world, "defendant")
    assert ok.status_code == 200 and ok.json()["result"] == "sent"
    assert _texts_to(world.bb, OPTED) == [] and len(_texts_to(world.bb, OK)) == 1


# ── DocuSeal's own SMS on resend is gated per signer ─────────────────────────

def test_docuseal_resend_suppresses_sms_only_for_opted_out_signer(world):
    from dashboard.routers import paperwork as pw

    _opt_out()
    packet = {"packet_id": "PKT-RS", "docuseal_submitters": [
        {"id": 1, "role": "indemnitor", "phone": f"+1{OPTED}", "status": "sent"},
        {"id": 2, "role": "coindemnitor", "phone": f"+1{OK}", "status": "sent"},
        {"id": 3, "role": "defendant", "phone": "", "status": "sent"},
    ]}
    svc = MagicMock(is_configured=True)
    svc.update_submitter = AsyncMock(side_effect=lambda sid, **kw: {"id": sid})
    svc.normalize_submitter_record = lambda raw: dict(raw or {})
    app = FastAPI()
    app.include_router(pw.paperwork_bp)
    with patch.object(pw, "_load_packet", AsyncMock(return_value=packet)), \
         patch.object(pw, "get_collection", return_value=AsyncMock()), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        r = TestClient(app).post("/api/paperwork/PKT-RS/docuseal/resend",
                                 json={"send_email": True, "send_sms": True})
    assert r.status_code == 200
    calls = {c.args[0]: c.kwargs for c in svc.update_submitter.await_args_list}
    assert calls[1] == {"send_email": True, "send_sms": False}      # opted out → email only
    assert calls[2] == {"send_email": True, "send_sms": True}       # everyone else unaffected
    assert calls[3] == {"send_email": True, "send_sms": False}      # unknown phone → fail closed
    reasons = {b["submitter_id"]: b["reason"] for b in r.json()["sms_blocked"]}
    assert reasons == {1: "recipient_opted_out", 3: "sms_recipient_phone_unknown"}


def test_docuseal_resend_sms_only_skips_opted_out_signer_entirely(world):
    from dashboard.routers import paperwork as pw

    _opt_out()
    packet = {"packet_id": "PKT-RS2", "docuseal_submitters": [
        {"id": 1, "role": "indemnitor", "phone": OPTED, "status": "sent"},
        {"id": 2, "role": "coindemnitor", "phone": OK, "status": "sent"},
    ]}
    svc = MagicMock(is_configured=True)
    svc.update_submitter = AsyncMock(side_effect=lambda sid, **kw: {"id": sid})
    svc.normalize_submitter_record = lambda raw: dict(raw or {})
    app = FastAPI()
    app.include_router(pw.paperwork_bp)
    with patch.object(pw, "_load_packet", AsyncMock(return_value=packet)), \
         patch.object(pw, "get_collection", return_value=AsyncMock()), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        r = TestClient(app).post("/api/paperwork/PKT-RS2/docuseal/resend",
                                 json={"send_email": False, "send_sms": True})
    assert [c.args[0] for c in svc.update_submitter.await_args_list] == [2]
    assert r.json()["resent"] == 1


# ── static audit: every BlueBubbles send goes through the gate ───────────────

_SEND_PATHS = ("/api/v1/message/text", "/api/v1/message/attachment", "/api/v1/message/schedule",
               "/api/v1/message/react", "/api/v1/chat/new", "/api/v1/message/multipart")
_GATE_CALLS = {"_consent_gate", "send_text"}


def _py_files():
    for base in ("dashboard", "api", "core", "services", "scripts", "writers", "scrapers"):
        d = ROOT / base
        if d.exists():
            yield from d.rglob("*.py")


def test_bluebubbles_send_endpoints_only_hit_from_gated_client_methods():
    """No module other than bb_private_api may call a BlueBubbles send endpoint,
    and inside BlueBubblesClient every method that does must call the gate."""
    offenders = []
    for f in _py_files():
        rel = str(f.relative_to(ROOT))
        tree = ast.parse(f.read_text(errors="ignore"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            hits = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and any(n.value.startswith(p) for p in _SEND_PATHS)]
            if not hits:
                continue
            if rel != "dashboard/routers/bb_private_api.py":
                offenders.append(f"{rel}::{fn.name} hits {hits}")
                continue
            posts = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                     and getattr(c.func, "attr", "") == "_request" and len(c.args) >= 2
                     and isinstance(c.args[0], ast.Constant) and c.args[0].value == "POST"
                     and isinstance(c.args[1], ast.Constant) and str(c.args[1].value).startswith(_SEND_PATHS)]
            if not posts:
                continue  # GET list / DELETE cancel of scheduled messages — not a send
            called = {getattr(c.func, "attr", getattr(c.func, "id", "")) for c in ast.walk(fn)
                      if isinstance(c, ast.Call)}
            if fn.name != "send_text" and not (_GATE_CALLS & called):
                offenders.append(f"{rel}::{fn.name} sends without _consent_gate")
            if fn.name == "send_text" and "_consent_gate" not in called:
                offenders.append(f"{rel}::send_text lost its _consent_gate")
    assert offenders == []


def test_bb_client_entry_points_gate_before_queueing():
    src = ast.parse((ROOT / "dashboard/services/bb_client.py").read_text())
    fns = {n.name: n for n in ast.walk(src) if isinstance(n, ast.AsyncFunctionDef)}
    for name in ("send_message_universal", "_send_attachment_direct"):
        called = [getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fns[name])
                  if isinstance(c, ast.Call)]
        assert "_consent_blocked" in called, name
    # the direct text helper relies on BlueBubblesClient.send_text (gated)
    called = {getattr(c.func, "attr", "") for c in ast.walk(fns["_send_message_direct"]) if isinstance(c, ast.Call)}
    assert "send_text" in called


def test_no_consent_override_knob_exists():
    """No purpose-based exemption and no staff override (owner decision)."""
    import inspect

    assert list(inspect.signature(ledger.check_send_allowed).parameters) == ["recipient", "purpose"]
    assert "_consent_checked" not in inspect.signature(BlueBubblesClient.send_text).parameters
    src = (ROOT / "dashboard/services/sms_consent_ledger.py").read_text()
    for knob in ("override", "exempt", "bypass", "allow_opted_out"):
        assert knob not in src.split('"""', 2)[-1].lower(), knob
