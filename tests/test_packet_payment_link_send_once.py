"""
Legacy static SwipeSimple link (packet_payment_link_service.maybe_send_packet_payment_link):
  - atomic send-once claim BEFORE sending (concurrent retries, >24h repeat,
    finalize → completion, stale claim → manual review, never auto-retried)
  - switch DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK default OFF → switch_off, no send
  - switch on but no STAFF-CONFIRMED premium (stored / 10% estimate never
    counts) → premium_unconfirmed, no send, no claim consumed
  - DocuSeal completion: flag unset → zero sends; flag on + confirmed → exactly one;
    flag on + unconfirmed → zero (stamped premium_unconfirmed).

All mocked: in-memory Mongo double, BlueBubbles send stub, Gmail off. No sends.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import dashboard.services.packet_payment_link_service as pls
from tests._fake_mongo import FakeCollection

ROOT = Path(__file__).resolve().parents[1]
PACKET_ID = "pkt-pay-0001"
BOOKING = "PAY-BK-0001"
PHONE = "2395550142"  # 555 test number


SWITCH_ENV = "DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK"
CONFIRMED = {
    pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 750.0,
    pls.PREMIUM_CONFIRMED_AT_FIELD: "2026-09-24T15:00:00+00:00",
    pls.PREMIUM_CONFIRMED_BY_FIELD: "staff-test",
}
UNCONFIRMED = {k: None for k in CONFIRMED}


def _packet(**kw):
    d = {"_id": "oid-pay-1", "packet_id": PACKET_ID, "booking_number": BOOKING,
         "premium_amount": 750.0, "indemnitor_phone": PHONE, "defendant_name": "Test Person",
         "esign_provider": "docuseal", "status": "pending_signature",
         "docuseal_submission_id": 777, **CONFIRMED}
    d.update(kw)
    return d


class World:
    def __init__(self, packet=None, bond=None):
        self.colls = defaultdict(FakeCollection)
        self.colls["paperwork_packets"] = FakeCollection([packet or _packet()])
        if bond is not None:
            self.colls["active_bonds"] = FakeCollection([bond])
        self.sent = []
        self.msgs = []

    def get_col(self, name):
        return self.colls[name]

    @contextmanager
    def patched(self, switch="true"):
        async def fake_bb(phone, msg):
            self.sent.append(phone)
            self.msgs.append(msg)
            await asyncio.sleep(0)
            return {"status": 200, "message": "sent"}

        env = {SWITCH_ENV: switch} if switch is not None else {}
        with patch.dict(os.environ, env), \
             patch.object(pls, "get_collection", side_effect=self.get_col), \
             patch.object(pls, "send_message_universal", side_effect=fake_bb), \
             patch("dashboard.services.gmail_reader.GmailReaderService") as gm:
            gm.return_value.is_configured = False
            yield self

    def packet(self):
        return self.colls["paperwork_packets"].docs[0]


def _send(**kw):
    kw.setdefault("packet_id", PACKET_ID)
    kw.setdefault("source", "docuseal_submission_completed")
    return pls.maybe_send_packet_payment_link(**kw)


# ── send-once ────────────────────────────────────────────────────────────────

def test_concurrent_redeliveries_send_exactly_once():
    w = World()
    with w.patched():
        async def go():
            return await asyncio.gather(*[_send() for _ in range(5)])
        results = asyncio.run(go())
    assert len(w.sent) == 1
    assert sum(1 for r in results if r.get("delivered")) == 1
    skipped = [r for r in results if r.get("skipped")]
    assert len(skipped) == 4
    assert all(r["reason"] in ("already_sent_once", "recently_sent") for r in skipped)
    assert w.packet()[pls.SEND_ONCE_FIELD]["state"] == "sent"


def test_repeat_after_25_hours_does_not_resend():
    w = World()
    with w.patched():
        first = asyncio.run(_send())
        assert first["delivered"] is True
        # push the 24h soft window out of the way: only the claim should block
        w.packet()["last_payment_link_sent_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        second = asyncio.run(_send())
    assert len(w.sent) == 1
    assert second["skipped"] is True and second["reason"] == "already_sent_once"
    assert second["manual_review"] is True


def test_finalize_then_completion_days_later_sends_once():
    w = World()
    with w.patched():
        asyncio.run(_send(source="packet_finalize"))
        w.packet()["last_payment_link_sent_at"] = (
            datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        r = asyncio.run(_send(source="docuseal_submission_completed"))
    assert len(w.sent) == 1 and r["reason"] == "already_sent_once"
    assert w.packet()[pls.SEND_ONCE_FIELD]["source"] == "packet_finalize"


def test_stale_claim_fails_closed_no_auto_retry():
    w = World(_packet(**{pls.SEND_ONCE_FIELD: {"state": "claimed", "claimed_at": "2026-09-01T00:00:00+00:00"}}))
    with w.patched():
        r = asyncio.run(_send())
    assert w.sent == []
    assert r["skipped"] and r["reason"] == "already_sent_once" and r["manual_review"] is True


def test_claim_error_fails_closed():
    w = World()
    with w.patched():
        w.colls["paperwork_packets"].find_one_and_update = AsyncMock(side_effect=RuntimeError("mongo down"))
        r = asyncio.run(_send())
    assert w.sent == [] and r["reason"] == "send_once_claim_error"


def test_send_exception_marks_manual_review_and_never_retries():
    w = World()
    with w.patched(), patch.object(pls, "send_swipesimple_payment_link",
                                   new=AsyncMock(side_effect=RuntimeError("boom"))) as send:
        r1 = asyncio.run(_send())
        r2 = asyncio.run(_send())
    assert send.await_count == 1
    assert r1["success"] is False and r2["reason"] == "already_sent_once"
    assert w.packet()[pls.SEND_ONCE_FIELD]["state"] == "send_error_manual_review"


def test_undelivered_send_is_not_retried():
    w = World()
    with w.patched(), patch.object(pls, "send_swipesimple_payment_link",
                                   new=AsyncMock(return_value={"success": True, "delivered": False})) as send:
        asyncio.run(_send())
        asyncio.run(_send())
    assert send.await_count == 1
    assert w.packet()[pls.SEND_ONCE_FIELD]["state"] == "not_delivered_manual_review"


def test_intake_promote_without_packet_claims_on_bond_booking():
    bond = {"booking_number": BOOKING, "premium": 300.0, "indemnitor_phone": PHONE,
            "payment_status": "pending", **CONFIRMED, pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 300.0}
    w = World(bond=bond)
    with w.patched():
        for _ in range(2):
            asyncio.run(pls.maybe_send_packet_payment_link(
                packet_id="", booking_number=BOOKING, amount=bond["premium"], bond_doc=bond,
                source="intake_promote"))
    assert len(w.sent) == 1
    assert w.colls["active_bonds"].docs[0][pls.SEND_ONCE_FIELD]["source"] == "intake_promote"


def test_no_key_fails_closed():
    w = World()
    with w.patched():
        r = asyncio.run(pls.maybe_send_packet_payment_link(
            amount=100.0, phone=PHONE, bond_doc={"indemnitor_phone": PHONE, **CONFIRMED},
            source="intake_promote"))
    assert w.sent == [] and r["reason"] == "send_once_no_key"


# ── switch (default OFF) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "off", "false", "0", "garbage"])
def test_switch_off_no_send_no_lookup(value):
    w = World()
    env = {SWITCH_ENV: value} if value is not None else {}
    with patch.dict(os.environ, env), \
         patch.object(pls, "_load_context", new=AsyncMock()) as load, \
         patch.object(pls, "send_swipesimple_payment_link", new=AsyncMock()) as send:
        if value is None:
            os.environ.pop(SWITCH_ENV, None)
        for source in ("intake_promote", "packet_finalize", "docuseal_submission_completed"):
            r = asyncio.run(_send(source=source, force=True))
            assert r["skipped"] is True and r["reason"] == "switch_off"
    load.assert_not_awaited()
    send.assert_not_awaited()
    assert pls.SEND_ONCE_FIELD not in w.packet()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled", "webhook", "all", "both"])
def test_any_enabled_value_enables_service_level_send(value):
    w = World()
    with w.patched(switch=value):
        r = asyncio.run(_send(source="packet_finalize"))
    assert r.get("delivered") is True and len(w.sent) == 1


# ── premium: STAFF-CONFIRMED only; stored / 10% estimates never count ─────────

@pytest.mark.parametrize("source", ["intake_promote", "packet_finalize", "docuseal_submission_completed"])
def test_estimated_or_stored_premium_is_unconfirmed(source):
    # premium_amount on the packet + the intake-promote 10% "premium" on the bond:
    # both look like real numbers but neither is staff-confirmed.
    pkt = _packet(**UNCONFIRMED, premium_amount=500.0, numeric_premium_dollar="$500.00")
    bond = {"booking_number": BOOKING, "bond_amount": 5000, "premium": 500.0,
            "premium_amount": 500.0, "total_premium": 500.0, "indemnitor_phone": PHONE}
    w = World(pkt, bond=bond)
    with w.patched():
        r = asyncio.run(_send(packet_doc=pkt, amount=500.0, source=source, force=True))
    assert r["skipped"] is True and r["reason"] == "premium_unconfirmed"
    assert "amount" not in r
    assert w.sent == []
    assert pls.SEND_ONCE_FIELD not in w.packet()      # no claim consumed by a skip


@pytest.mark.parametrize("missing", list(CONFIRMED))
def test_partial_confirmation_is_unconfirmed(missing):
    pkt = _packet(**{missing: None})
    w = World(pkt)
    with w.patched():
        r = asyncio.run(_send(packet_doc=pkt))
    assert r["reason"] == "premium_unconfirmed" and w.sent == []


def test_confirmed_premium_on_bond_counts_and_caller_amount_ignored():
    pkt = _packet(**UNCONFIRMED)
    bond = {"booking_number": BOOKING, "indemnitor_phone": PHONE, "premium": 500.0,
            **CONFIRMED, pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 612.5}
    w = World(pkt, bond=bond)
    with w.patched():
        r = asyncio.run(_send(packet_doc=pkt, amount=999.0))
    assert r["delivered"] is True and len(w.sent) == 1
    assert "$612.50" in w.msgs[0] and "999" not in w.msgs[0] and "$500" not in w.msgs[0]


def test_confirmed_premium_helper():
    assert pls.confirmed_premium(None, None) == 0.0
    assert pls.confirmed_premium({"premium": 500, "premium_amount": 500}, {"total_premium": 500}) == 0.0
    assert pls.confirmed_premium(CONFIRMED, None) == 750.0
    assert pls.confirmed_premium({**CONFIRMED, pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: "1,250.00"}, None) == 1250.0
    assert pls.confirmed_premium({**CONFIRMED, pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 0}, None) == 0.0
    assert pls.confirmed_premium({**CONFIRMED, pls.PREMIUM_CONFIRMED_BY_FIELD: "  "}, None) == 0.0


def test_resolve_premium_never_uses_stored_or_derived_amounts():
    """Staff endpoint copy: explicit staff amount, else confirmed, else 0 ("Confirmed Amount")."""
    assert pls._resolve_premium(None, None, {"bond_amount": 5000}, None) == 0.0
    assert pls._resolve_premium(None, {"bond_amount": 10000}, {"bond_amount": "2,500"}, {"bond_amount": 1}) == 0.0
    # stored premium fields may be the 10% estimate → never quoted as "Confirmed"
    assert pls._resolve_premium(None, None, {"bond_amount": 5000, "premium_amount": "612.50", "premium": 500}, None) == 0.0
    assert pls._resolve_premium(None, {"premium_amount": 500, "numeric_premium_dollar": "$500"}, None, {"premium": 500}) == 0.0
    assert pls._resolve_premium(None, None, {**CONFIRMED}, None) == 750.0
    assert pls._resolve_premium("300", None, {**CONFIRMED}, None) == 300.0    # explicit staff entry wins


def test_staff_endpoint_without_amount_does_not_quote_estimate():
    bond = {"booking_number": BOOKING, "bond_amount": 5000, "premium": 500.0, "indemnitor_phone": PHONE}
    w = World(bond=bond)
    with w.patched(switch=None):
        os.environ.pop(SWITCH_ENV, None)
        r = asyncio.run(pls.send_swipesimple_payment_link(
            booking_number=BOOKING, amount=0, phone=PHONE, deliver_email=False,
            source="staff_swipesimple_link"))
    assert r["delivered"] is True            # staff action is NOT behind the switch
    assert "$500" not in w.msgs[0] and "Confirmed Amount" in w.msgs[0]


def test_premium_unconfirmed_log_has_no_amount_or_pii(caplog):
    caplog.set_level("INFO")
    pkt = _packet(**UNCONFIRMED, premium_amount=500.0, bond_amount=5000)
    w = World(pkt)
    with w.patched():
        asyncio.run(_send(packet_doc=pkt))
    assert "reason=premium_unconfirmed" in caplog.text
    for needle in ("5000", "500.0", "500", PHONE, "Test Person", BOOKING):
        assert needle not in caplog.text


def _mults_by_tenth(tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value in (0.1, 10, 10.0):
                    hits.append(ast.unparse(node))
    return hits


def test_no_ten_percent_computation_in_legacy_link_path():
    svc = ast.parse((ROOT / "dashboard/services/packet_payment_link_service.py").read_text())
    assert _mults_by_tenth(svc) == []
    # intake promote call site: the maybe_send_packet_payment_link(...) call has no 10% expression
    intake = ast.parse((ROOT / "dashboard/routers/intake.py").read_text())
    calls = [n for n in ast.walk(intake) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "maybe_send_packet_payment_link"]
    assert calls, "intake promote call site not found"
    for c in calls:
        assert _mults_by_tenth(c) == [], ast.unparse(c)
    # shared completion handler never computes an amount either
    dc_src = ast.parse((ROOT / "dashboard/services/docuseal_completion.py").read_text())
    assert _mults_by_tenth(dc_src) == []


# ── DocuSeal completion end-to-end (real service, real handler) ──────────────

SECRET = "docuseal-test-secret-not-real"


def _sig(body):
    ts = int(time.time())
    return f"{ts}." + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def _post_many(w, n, env):
    from fastapi import FastAPI
    import httpx
    from dashboard.routers.webhooks import webhooks_bp

    app = FastAPI()
    app.include_router(webhooks_bp)
    body = json.dumps({"event_type": "submission.completed", "data": {"id": 777}}).encode()

    class _DS:
        is_configured = False

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await asyncio.gather(*[
                client.post("/api/webhooks/docuseal", content=body,
                            headers={"Content-Type": "application/json", "X-DocuSeal-Signature": _sig(body)})
                for _ in range(n)])

    with patch.dict(os.environ, {"DOCUSEAL_WEBHOOK_SECRET": SECRET, **env}), \
         patch("dashboard.routers.webhooks.get_collection", side_effect=w.get_col), \
         patch("dashboard.services.docuseal_service.DocuSealService", _DS), \
         patch("dashboard.routers.events.publish_event", new=AsyncMock()), \
         patch("dashboard.services.bond_court_seed_service.seed_court_calendar_for_bond",
               new=AsyncMock(return_value={})), \
         patch("dashboard.services.swipesimple_invoice_service.maybe_issue_share_invoice_for_bond",
               new=AsyncMock(return_value={"ok": True})):
        for k in ("SLACK_WEBHOOK_LEADS", "SLACK_WEBHOOK_URL"):
            os.environ.pop(k, None)
        return asyncio.run(go())


def test_completion_flag_unset_zero_legacy_sends():
    w = World()
    with w.patched():
        os.environ.pop("DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK", None)
        rs = _post_many(w, 3, {})
    assert all(r.status_code == 200 for r in rs)
    assert w.sent == []
    assert pls.SEND_ONCE_FIELD not in w.packet()


def test_completion_flag_on_unconfirmed_premium_zero_sends():
    from dashboard.services import docuseal_completion as dc

    w = World(_packet(**UNCONFIRMED))
    with w.patched():
        rs = _post_many(w, 3, {SWITCH_ENV: "true"})
    assert all(r.status_code == 200 for r in rs)
    assert w.sent == []
    step = w.packet()["docuseal_completion"]["steps"][dc.STEP_LEGACY]
    assert step["state"] == "skipped" and step["reason"] == "premium_unconfirmed"
    assert pls.SEND_ONCE_FIELD not in w.packet()


def test_completion_flag_on_concurrent_redeliveries_exactly_one_send():
    w = World()
    with w.patched():
        rs = _post_many(w, 4, {"DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK": "true"})
        # a 25-hour-later redelivery / finalize retry still cannot resend
        w.packet()["last_payment_link_sent_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        r_late = asyncio.run(_send(source="packet_finalize"))
    assert sorted(r.json()["action"] for r in rs).count("completion_accepted") == 1
    assert len(w.sent) == 1
    assert r_late["reason"] == "already_sent_once"
