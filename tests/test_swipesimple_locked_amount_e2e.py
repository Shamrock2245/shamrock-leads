"""
End-to-end proof: BondCase premium -> locked SwipeSimple invoice, for EVERY
lead source, through the ONE service entrypoint
(swipesimple_invoice_service.maybe_issue_share_invoice_for_bond ->
create_locked_invoice -> _share_invoice_http -> build_create_invoice_form).

Asserts, per source:
  * invoice[amount] / [price] cents == BondCase premium cents (never typed,
    defaulted, rounded or estimated)
  * invoice[reference_id] == exact booking #
  * exactly one SwipeSimple create per bond (atomic claim; repeat = no HTTP)
  * stage-only: dispatch is None, no BlueBubbles / email send
Fail-closed matrix: missing / zero / negative / non-numeric / sub-cent /
ambiguous premium, intake-promote 10% estimate, missing booking # -> raises a
reason code, never calls SwipeSimple, never takes a claim.

Offline only: SwipeSimple HTTP, Mongo and BlueBubbles are mocked. No network,
no real card, no LIVE invoice, no texts. SWIPESIMPLE_LIVE is set only inside
this process so the gated code path runs against the mocks.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dashboard.services.swipesimple_invoice_service as ss
from dashboard.services import payment_links
from dashboard.services.docuseal_share_invoice import resolve_share_invoice_bond_id
from dashboard.services.swipesimple_invoice_service import (
    SwipeSimpleInvoiceError,
    maybe_issue_share_invoice_for_bond,
    resolve_locked_premium,
)
from tests.test_swipesimple_invoice_claims import FakeClaims

REPO = pathlib.Path(__file__).resolve().parents[1]
PAY_LINK = "https://swipesimple.com/invoices/inv_e2e_test/payment"

# Every lead source the business takes bonds from (canonical + common aliases
# as they appear on intake / bond / packet docs).
LEAD_SOURCES = [
    "wix_webhook",          # website indemnitor starting a bond (Wix wizard -> CRM)
    "website",
    "telegram",             # Telegram bot
    "telegram_mini_app",    # Netlify mini-apps
    "walk_in",              # manual staff walk-in entry
    "manual_entry",
    "scraper",              # sheriff / clerk hydrate
    "id_scan",              # ID scan
    "kiosk",                # in-office tablet
    "shannon_voice",        # Shannon (ElevenLabs) intake
    "sms",                  # BlueBubbles / Twilio texts
    "docuseal_submission_completed",  # DocuSeal completed hook
]


@pytest.fixture(autouse=True)
def _gates(monkeypatch):
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")          # mocked HTTP only
    monkeypatch.delenv("SWIPESIMPLE_DISPATCH_LIVE", raising=False)
    for k in ("SWIPESIMPLE_SESSION", "SWIPESIMPLE_COOKIE_JAR", "SWIPESIMPLE_CSRF_TOKEN"):
        monkeypatch.delenv(k, raising=False)


class Rig:
    """Real service code; Mongo / SwipeSimple HTTP / BlueBubbles mocked."""

    def __init__(self, bond: Dict[str, Any]):
        self.bond = dict(bond)
        self.claims = FakeClaims()
        self.forms: List[Dict[str, str]] = []
        self.sends = AsyncMock(side_effect=AssertionError("customer send attempted"))

    async def _load(self, _bond_id):
        return dict(self.bond, _collection="bond_cases")

    async def _claims_coll(self):
        return self.claims

    async def _csrf(self, _cfg):
        return "csrf-test"

    async def _create(self, *, form_fields, cfg):
        self.forms.append(dict(form_fields))
        return {"status_code": 302}

    async def _resolve(self, **_kw):
        return "inv_e2e_test"

    async def _copy(self, **_kw):
        return PAY_LINK

    async def _persist(self, bond, *, bond_id, booking_number, payment_link, invoice_id):
        self.bond.update(
            swipesimple_payment_link=payment_link,
            swipesimple_invoice_id=invoice_id,
            swipesimple_invoice_number=booking_number,
            swipesimple_invoice_bond_id=bond_id,
        )

    def patches(self):
        cfg = {"has_session": True, "base_url": "https://swipesimple.com"}
        return [
            patch.object(ss, "_load_bond_by_id", self._load),
            patch.object(ss, "_claims_collection", self._claims_coll),
            patch.object(ss, "_fetch_csrf", self._csrf),
            patch.object(ss, "_http_create_invoice", self._create),
            patch.object(ss, "_resolve_invoice_id_after_create", self._resolve),
            patch.object(ss, "_http_copy_link", self._copy),
            patch.object(ss, "_persist_invoice_fields", self._persist),
            patch.object(ss, "load_swipesimple_session_config", lambda: cfg),
            patch.object(ss, "_send_dispatch", self.sends),
            patch("dashboard.services.bb_client.send_message_universal", self.sends),
        ]

    async def issue(self, bond_id: str, source: str):
        ps = self.patches()
        for p in ps:
            p.start()
        try:
            return await maybe_issue_share_invoice_for_bond(
                bond_id, channel="imessage", dispatch=False, source=source,
            )
        finally:
            for p in reversed(ps):
                p.stop()


def _bond(lead_source: str, /, **money) -> Dict[str, Any]:
    doc = {
        "bond_case_id": f"BC-{lead_source}",
        "booking_number": f"BK-{lead_source}-2026-0042",
        "intake_source": lead_source,
        "defendant_name": "Test Defendant",
        "indemnitor_name": "Test Indemnitor",
        "indemnitor_phone": "2395550100",
        "indemnitor_email": "indemnitor@example.invalid",
        "bond_amount": 12345.0,
    }
    doc.update(money)
    return doc


def _bond_id_for(source: str, bond: Dict[str, Any]) -> str:
    """How each entry point names the bond when it calls the service."""
    if source.startswith("docuseal"):
        packet = {"packet_id": "PKT-1", "bond_case_id": bond["bond_case_id"]}
        bond_id, reason = resolve_share_invoice_bond_id(packet)
        assert reason == "ok"
        return bond_id
    return bond["bond_case_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Same path, same locked amount, for every source
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", LEAD_SOURCES)
@pytest.mark.parametrize(
    "money,expected_cents",
    [
        ({"premium_cents": 123457}, 123457),                     # staff Write Bond (AR)
        ({"premium_cents": 123457, "premium": 1234.57}, 123457),  # AR writes both, agree
        ({"premium_amount": "1,250.50"}, 125050),                # legacy BondCase field
        ({"premium": 100.0}, 10000),                             # FL $100 minimum premium
        # staff-confirmed premium beats the intake-promote 10% estimate
        ({"premium": 1234.5, "source": "intake_promotion", "premium_is_estimate": True,
          "premium_confirmed_amount": "1500.00", "premium_confirmed_at": "2026-10-08T12:00:00Z",
          "premium_confirmed_by": "staff:test"}, 150000),
    ],
)
def test_every_source_stages_same_locked_invoice(source, money, expected_cents):
    bond = _bond(source, **money)
    rig = Rig(bond)
    out = asyncio.run(rig.issue(_bond_id_for(source, bond), source))

    assert out["ok"] is True
    assert out["dispatch"] is None                      # stage only
    assert len(rig.forms) == 1                          # exactly one create
    form = rig.forms[0]
    assert form["invoice[amount]"] == str(expected_cents)
    assert form["invoice[items][][price]"] == str(expected_cents)
    assert form["invoice[unadjusted_amount]"] == str(expected_cents)
    assert form["invoice[reference_id]"] == bond["booking_number"]
    assert form["invoice[items][][id]"] == "im_bae23df0a0cb4e01a688bdd75cc96bf1"
    assert out["create"]["invoice_number"] == bond["booking_number"]
    assert Decimal(str(out["create"]["premium_amount"])) * 100 == expected_cents
    rig.sends.assert_not_called()

    # Second call (any source / retry / redelivery): no second SwipeSimple create.
    again = asyncio.run(rig.issue(_bond_id_for(source, bond), "retry"))
    assert again["create"]["idempotent"] is True
    assert len(rig.forms) == 1
    # Every pay-by-card offer for this case now resolves to its OWN locked link.
    assert payment_links.payment_link_for(source, rig.bond) == PAY_LINK


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_dispatch_stays_dry_run_without_dispatch_live(source):
    bond = _bond(source, premium_cents=50000)
    rig = Rig(bond)
    ps = rig.patches()
    for p in ps:
        p.start()
    try:
        out = asyncio.run(maybe_issue_share_invoice_for_bond(
            bond["bond_case_id"], dispatch=True, source=source))
    finally:
        for p in reversed(ps):
            p.stop()
    assert out["dispatch"]["dry_run"] is True
    assert out["dispatch"]["sent"] is False
    rig.sends.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Fail closed: no invoice, clear reason, no SwipeSimple call, no claim
# ─────────────────────────────────────────────────────────────────────────────

FAIL_CLOSED = [
    ({}, "premium_missing_on_bondcase"),
    ({"premium": ""}, "premium_missing_on_bondcase"),
    ({"premium": 0}, "premium_zero"),
    ({"premium_amount": "0.00"}, "premium_zero"),
    ({"premium_cents": 0}, "premium_zero"),
    ({"premium": -100}, "premium_negative"),
    ({"premium": "TBD"}, "premium_not_numeric_on_bondcase"),
    ({"premium": 123.456}, "premium_not_exact_cents"),
    ({"premium": 2345.67 * 0.10}, "premium_not_exact_cents"),       # float noise, no rounding
    ({"premium_amount": 0, "premium": 1500}, "premium_zero"),
    ({"premium_amount": 1000, "total_premium": 1500}, "premium_ambiguous_on_bondcase"),
    ({"premium_cents": 150000, "premium_amount": 1000}, "premium_ambiguous_on_bondcase"),
    # exactly what intake promote writes: 10% of bond, flagged estimate
    ({"premium": 5000.0 * 0.10, "source": "intake_promotion",
      "premium_is_estimate": True}, "premium_estimate_unconfirmed"),
    # pre-flag promoted bonds already in Mongo (source only)
    ({"premium": 500.0, "source": "intake_promotion"}, "premium_estimate_unconfirmed"),
    # confirmation trio incomplete -> does not count
    ({"premium": 500.0, "source": "intake_promotion",
      "premium_confirmed_amount": "600.00"}, "premium_estimate_unconfirmed"),
]


@pytest.mark.parametrize("source", ["wix_webhook", "telegram", "walk_in", "docuseal_submission_completed"])
@pytest.mark.parametrize("money,code", FAIL_CLOSED)
def test_fail_closed_never_calls_swipesimple(source, money, code):
    bond = _bond(source, **money)
    rig = Rig(bond)
    with pytest.raises(SwipeSimpleInvoiceError, match=code):
        asyncio.run(rig.issue(_bond_id_for(source, bond), source))
    assert rig.forms == []
    assert rig.claims.docs == []
    rig.sends.assert_not_called()


def test_fail_closed_missing_booking_number():
    bond = _bond("walk_in", premium_cents=10000)
    bond.pop("booking_number")
    rig = Rig(bond)
    with pytest.raises(SwipeSimpleInvoiceError, match="missing_booking_number"):
        asyncio.run(rig.issue(bond["bond_case_id"], "walk_in"))
    assert rig.forms == [] and rig.claims.docs == []


def test_staff_write_bond_unlocks_promoted_bond_at_exact_cents():
    """Promote writes the 10% estimate (blocked); staff Write Bond sets premium_cents."""
    promoted = _bond("wix_webhook", premium=12345.0 * 0.10, source="intake_promotion",
                     premium_is_estimate=True)
    with pytest.raises(SwipeSimpleInvoiceError, match="premium_estimate_unconfirmed"):
        resolve_locked_premium(promoted)
    from dashboard.services.ar_math import money_fields

    promoted.update(money_fields("1500.00", "0"))      # what Write Bond stores
    assert resolve_locked_premium(promoted) == Decimal("1500.00")


def test_intake_promote_marks_premium_as_estimate():
    """Contract: the promote bond_doc that writes premium = bond * 0.10 flags it."""
    tree = ast.parse((REPO / "dashboard/routers/intake.py").read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value: v for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "premium" in keys and "insurance_company" in keys and "poa_full" in keys:
                flag = keys.get("premium_is_estimate")
                assert isinstance(flag, ast.Constant) and flag.value is True
                found = True
    assert found, "intake promote bond_doc not found"


# ─────────────────────────────────────────────────────────────────────────────
# Pay-by-card offers outside the invoice service use the same resolver
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", LEAD_SOURCES + ["intake_promotion", ""])
def test_pre_bond_offer_is_swipesimple_for_every_source(source):
    """Before a bond/premium exists, every source gets a SwipeSimple link (no amount)."""
    link = payment_links.payment_link_for(source)
    assert link.startswith("https://swipesimple.com/links/lnk_")
    assert "amount=" not in link


def test_estimate_website_link_is_never_a_case_invoice_link():
    bond = {"payment_link": "https://shamrockbailbonds.biz/payment?amount=50.0&booking=BK"}
    assert payment_links.case_invoice_link(bond) is None
    assert ss._existing_payment_link(bond) is None
    assert payment_links.payment_link_for("website", bond).startswith("https://swipesimple.com/links/")


def test_indemnitor_payment_link_endpoint_never_estimates_or_writes():
    from dashboard.routers import indemnitors

    bond = _bond("wix_webhook", premium=1234.5, source="intake_promotion", premium_is_estimate=True)
    active = MagicMock()
    active.find_one = AsyncMock(return_value=dict(bond))
    active.update_one = AsyncMock(side_effect=AssertionError("must not write"))
    prospective = MagicMock()
    prospective.find_one = AsyncMock(return_value=None)
    prospective.update_one = AsyncMock(side_effect=AssertionError("must not write"))
    colls = {"active_bonds": active, "prospective_bonds": prospective}
    with patch.object(indemnitors, "get_collection", lambda name: colls[name]):
        out = asyncio.run(indemnitors.api_indemnitor_payment_link(bond["booking_number"]))
    assert out["success"] is True
    assert out["premium"] is None and out["amount_locked"] is False
    assert out["payment_link"].startswith("https://swipesimple.com/links/lnk_")
    assert "amount=" not in out["payment_link"]

    # After staff Write Bond (premium + premium_cents agree) and a staged invoice.
    staged = dict(bond, premium=1500.0, premium_cents=150000, swipesimple_payment_link=PAY_LINK)
    active.find_one = AsyncMock(return_value=staged)
    with patch.object(indemnitors, "get_collection", lambda name: colls[name]):
        out = asyncio.run(indemnitors.api_indemnitor_payment_link(bond["booking_number"]))
    assert out["payment_link"] == PAY_LINK
    assert out["amount_locked"] is True and out["premium"] == 1500.0


def test_client_portal_payment_link_prefers_staged_invoice():
    from dashboard.routers import client_portal

    staged = _bond("telegram", premium_cents=150000, swipesimple_payment_link=PAY_LINK)
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=staged)
    token = {"booking_number": staged["booking_number"], "defendant_name": "T", "source": "telegram"}
    with patch.object(client_portal, "validate_token", AsyncMock(return_value=token)), \
         patch.object(client_portal, "get_collection", lambda _n: coll):
        out = asyncio.run(client_portal.portal_payment_link("tok"))
    assert out["payment_link"] == PAY_LINK


def test_staff_send_refuses_typed_amount_that_differs_from_locked_invoice():
    from dashboard.services import packet_payment_link_service as pps

    staged = _bond("walk_in", premium_cents=150000, swipesimple_payment_link=PAY_LINK)
    send = AsyncMock(side_effect=AssertionError("must not send"))
    with patch.object(pps, "get_collection", MagicMock()), \
         patch.object(pps, "send_message_universal", send):
        out = asyncio.run(pps.send_swipesimple_payment_link(
            booking_number=staged["booking_number"], amount="1234.50",
            phone="2395550100", bond_doc=staged, deliver_email=False,
            source="staff_swipesimple_link",
        ))
    assert out["success"] is False
    assert out["error"] == "amount_mismatch_locked_invoice"
    assert out["locked_premium"] == 1500.0
    send.assert_not_called()
