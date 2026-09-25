"""
Intake promote + packet finalize legacy payment-link AUTO sends
(dashboard/routers/intake.py::_promote_auto_payment_link,
 dashboard/routers/paperwork.py::_finalize_auto_payment_link):

  - behind the SAME switch as DocuSeal completion
    (DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK, DEFAULT OFF → no send);
    any enabled value (webhook-style or "all") enables these paths
  - switch on + unconfirmed / 10%-estimate premium → skip premium_unconfirmed
  - switch on + staff-confirmed premium → exactly one send (send-once claim)

All mocked: in-memory Mongo double, BlueBubbles stub, Gmail off. No sends.
"""
from __future__ import annotations

import ast
import asyncio
import os
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import dashboard.services.packet_payment_link_service as pls
from dashboard.routers.intake import _promote_auto_payment_link
from dashboard.routers.paperwork import _finalize_auto_payment_link
from tests._fake_mongo import FakeCollection

ROOT = Path(__file__).resolve().parents[1]
SWITCH_ENV = "DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK"
PACKET_ID = "pkt-cs-0001"
BOOKING = "CS-BK-0001"
PHONE = "2395550143"  # 555 test number
DEFENDANT = "Casey Callsite"
CONFIRMED = {
    pls.PREMIUM_CONFIRMED_AMOUNT_FIELD: 875.0,
    pls.PREMIUM_CONFIRMED_AT_FIELD: "2026-09-24T15:00:00+00:00",
    pls.PREMIUM_CONFIRMED_BY_FIELD: "staff-test",
}


def _packet(**kw):
    # packet_builder_service writes an estimated premium_amount (10% of bond)
    d = {"_id": "oid-cs-1", "packet_id": PACKET_ID, "booking_number": BOOKING,
         "premium_amount": 500.0, "numeric_premium_dollar": "$500.00",
         "indemnitor_phone": PHONE, "defendant_name": DEFENDANT}
    d.update(kw)
    return d


def _promote_bond_doc(**kw):
    # exactly what intake_promote builds: premium = bond_amount * 0.10 (estimate)
    d = {"booking_number": BOOKING, "defendant_name": DEFENDANT, "bond_amount": 5000.0,
         "premium": 5000.0 * 0.10, "indemnitor_phone": PHONE, "indemnitor_email": "",
         "paperwork_packet_id": PACKET_ID, "payment_status": "pending"}
    d.update(kw)
    return d


class World:
    def __init__(self, packet):
        self.colls = defaultdict(FakeCollection)
        self.colls["paperwork_packets"] = FakeCollection([packet])
        self.sent = []

    def get_col(self, name):
        return self.colls[name]

    @contextmanager
    def patched(self, switch):
        async def fake_bb(phone, msg):
            self.sent.append(msg)
            return {"status": 200, "message": "sent"}

        with patch.dict(os.environ, {}), \
             patch.object(pls, "get_collection", side_effect=self.get_col), \
             patch.object(pls, "send_message_universal", side_effect=fake_bb), \
             patch("dashboard.services.gmail_reader.GmailReaderService") as gm:
            gm.return_value.is_configured = False
            if switch is None:
                os.environ.pop(SWITCH_ENV, None)
            else:
                os.environ[SWITCH_ENV] = switch
            yield self

    def packet(self):
        return self.colls["paperwork_packets"].docs[0]


def _promote(bond_doc=None):
    return _promote_auto_payment_link(bond_doc=bond_doc or _promote_bond_doc(), intake_doc={},
                                      matched_booking=BOOKING, defendant_name=DEFENDANT)


def _finalize(packet):
    return _finalize_auto_payment_link(PACKET_ID, packet)


PATHS = {
    "intake_promote": lambda pkt: _promote(),
    "packet_finalize": lambda pkt: _finalize(pkt),
}


# ── switch OFF (default) → no send ───────────────────────────────────────────

@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("value", [None, "", "0", "false", "off", "disabled", "garbage"])
def test_switch_off_no_send(path, value):
    pkt = _packet(**CONFIRMED)                       # even a confirmed premium does not send
    w = World(pkt)
    with w.patched(value), patch.object(pls, "maybe_send_packet_payment_link", new=AsyncMock()) as ms:
        r = asyncio.run(PATHS[path](pkt))
    assert r["skipped"] is True and r["reason"] == "switch_off" and r["source"] == path
    ms.assert_not_awaited()
    assert w.sent == [] and pls.SEND_ONCE_FIELD not in w.packet()


@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled", "webhook", "all", "both"])
def test_any_enabled_value_enables_path(path, value):
    pkt = _packet()
    w = World(pkt)
    with w.patched(value), patch.object(pls, "maybe_send_packet_payment_link",
                                        new=AsyncMock(return_value={"skipped": True})) as ms:
        asyncio.run(PATHS[path](pkt))
    ms.assert_awaited_once()
    kw = ms.await_args.kwargs
    assert kw["source"] == path
    if path == "intake_promote":
        assert kw["amount"] is None                  # never the 10% estimate


# ── switch ON + unconfirmed / estimate premium → premium_unconfirmed ─────────

@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("value", ["true", "all"])
def test_switch_on_estimate_premium_skips_premium_unconfirmed(path, value, caplog):
    caplog.set_level("INFO")
    pkt = _packet()                                  # only estimated premiums present
    w = World(pkt)
    with w.patched(value):
        r = asyncio.run(PATHS[path](pkt))
        r2 = asyncio.run(PATHS[path](pkt))
    assert r["skipped"] is True and r["reason"] == "premium_unconfirmed"
    assert r2["reason"] == "premium_unconfirmed"     # no claim consumed → still just skipped
    assert "amount" not in r
    assert w.sent == [] and pls.SEND_ONCE_FIELD not in w.packet()
    assert "reason=premium_unconfirmed" in caplog.text
    for needle in ("500", PHONE, DEFENDANT, BOOKING):
        assert needle not in caplog.text


@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("missing", list(CONFIRMED))
def test_switch_on_partial_confirmation_skips(path, missing):
    pkt = _packet(**{**CONFIRMED, missing: None})
    w = World(pkt)
    with w.patched("true"):
        r = asyncio.run(PATHS[path](pkt))
    assert r["reason"] == "premium_unconfirmed" and w.sent == []


# ── switch ON + staff-confirmed premium → exactly one send ───────────────────

@pytest.mark.parametrize("path", list(PATHS))
def test_switch_on_confirmed_premium_exactly_one_send(path):
    pkt = _packet(**CONFIRMED)
    w = World(pkt)
    with w.patched("true"):
        async def go():
            return await asyncio.gather(*[PATHS[path](pkt) for _ in range(4)])
        results = asyncio.run(go())
        other = "packet_finalize" if path == "intake_promote" else "intake_promote"
        later = asyncio.run(PATHS[other](pkt))       # the other auto path is blocked too
    assert len(w.sent) == 1
    assert "$875.00" in w.sent[0] and "$500" not in w.sent[0]
    assert sum(1 for r in results if r.get("delivered")) == 1
    assert w.packet()[pls.SEND_ONCE_FIELD]["state"] == "sent"
    assert w.packet()[pls.SEND_ONCE_FIELD]["source"] == path
    assert later["skipped"] is True and later["reason"] in ("already_sent_once", "recently_sent")


# ── call sites really use the gated helpers ──────────────────────────────────

def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _called_names(fn):
    return {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fn) if isinstance(c, ast.Call)}


def test_endpoints_route_through_gated_helpers():
    intake = ast.parse((ROOT / "dashboard/routers/intake.py").read_text())
    promote = _called_names(_func(intake, "intake_promote"))
    assert "_promote_auto_payment_link" in promote
    assert "maybe_send_packet_payment_link" not in promote
    paperwork = ast.parse((ROOT / "dashboard/routers/paperwork.py").read_text())
    finalize = _called_names(_func(paperwork, "packet_builder_finalize"))
    assert "_finalize_auto_payment_link" in finalize
    assert "maybe_send_packet_payment_link" not in finalize
    # the staff manual endpoint stays an explicit staff action (not switch-gated)
    staff = _called_names(_func(paperwork, "generate_swipesimple_link"))
    assert "send_swipesimple_payment_link" in staff
    assert "legacy_payment_link_enabled" not in staff


def test_nothing_in_app_code_sets_premium_confirmed_fields():
    """No setter exists yet (no staff confirmation UI): nothing may auto-mark a
    premium as confirmed. App code may only READ the field names via the
    service constants. If a staff-authenticated setter is added later, update
    this test deliberately."""
    import re

    field_re = re.compile(r"^premium_confirmed_\w+$")
    offenders = []
    for base in ("dashboard", "api", "core", "services", "scripts"):
        for f in (ROOT / base).rglob("*.py"):
            tree = ast.parse(f.read_text(errors="ignore"))
            rel = str(f.relative_to(ROOT))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and field_re.match(node.value):
                    # only the constant definitions in the service may spell the names
                    if rel != "dashboard/services/packet_payment_link_service.py":
                        offenders.append(f"{rel}: literal {node.value}")
                if isinstance(node, ast.Dict):
                    for k in node.keys:
                        if isinstance(k, ast.Name) and k.id.startswith("PREMIUM_CONFIRMED"):
                            offenders.append(f"{rel}: dict key {k.id}")
                        if isinstance(k, ast.Constant) and isinstance(k.value, str) and "premium_confirmed" in k.value:
                            offenders.append(f"{rel}: dict key {k.value}")
    assert offenders == []
    for name in ("premium_confirmed_amount", "premium_confirmed_at", "premium_confirmed_by"):
        assert name in {pls.PREMIUM_CONFIRMED_AMOUNT_FIELD, pls.PREMIUM_CONFIRMED_AT_FIELD,
                        pls.PREMIUM_CONFIRMED_BY_FIELD}
