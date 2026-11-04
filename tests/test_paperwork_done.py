"""Completion page: pay step only when signature and identity safeguards allow it.

TEST- packet data only. No SwipeSimple HTTP, no DocuSeal send, no texts.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from dashboard.main import app
from dashboard.services.swipesimple_invoice_service import _DEFAULT_BASE_URL
from tests._inmem_mongo import matches

client = TestClient(app)

PACKET_ID = "PKT-TEST-DONE"
BOOKING = "TEST-BOOK-1"
CASE = "CASE-TEST-1"
NAME = "TEST PERSON"
COSIGNER = "TEST COSIGNER"
DEFENDANT = "TEST DEFENDANT"


def _pay_url() -> str:
    base = (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/")
    return f"{base}/links/TEST-done"


def _packet(**over) -> dict:
    doc = {
        "packet_id": PACKET_ID,
        "status": "partially_signed",
        "voided": False,
        "booking_number": BOOKING,
        "bond_case_id": CASE,
        "indemnitor_name": NAME,
        "indemnitor_id": "IND-TEST-1",
        "docuseal_submitters": [
            {"role": "indemnitor", "status": "completed", "name": NAME},
            {"role": "coindemnitor", "status": "awaiting", "name": COSIGNER},
            {"role": "defendant", "status": "sent", "name": DEFENDANT},
        ],
    }
    doc.update(over)
    return doc


def _bond(**over) -> dict:
    doc = {
        "bond_case_id": CASE,
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": NAME,
        "indemnitor_id": "IND-TEST-1",
        "indemnitors": [{"role": "indemnitor", "name": NAME, "indemnitor_id": "IND-TEST-1"}],
        "premium_confirmed_amount": "1500.00",
        "premium_confirmed_at": "2026-11-04T12:00:00+00:00",
        "premium_confirmed_by": "test-agent",
        "down_payment_amount": "500.00",
        "premium_paid": False,
        "invoice_payment_link": _pay_url(),
        "swipesimple_invoice_amount_cents": 50000,
        "swipesimple_invoice_number": BOOKING,
        "swipesimple_invoice_bond_id": CASE,
    }
    doc.update(over)
    return doc


def _with_scan(packet: dict) -> dict:
    doc = copy.deepcopy(packet)
    doc["id_ocr"] = {
        "success": True,
        "role": "indemnitor",
        "full_name": NAME,
        "dl_state": "FL",
    }
    return doc


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=500):
        return list(self._rows)[:length]


class _Col:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, flt=None, *args, **kwargs):
        for doc in self.docs:
            if matches(doc, flt or {}):
                return copy.deepcopy(doc)
        return None

    def find(self, flt=None, projection=None):
        rows = [copy.deepcopy(doc) for doc in self.docs if matches(doc, flt or {})]
        return _Cursor(rows)


@contextmanager
def _db(packet=None, bond=None, *, boom=False, bond_boom=False):
    store: dict = {}

    def get_collection(name):
        if boom or (bond_boom and name in {"bond_cases", "active_bonds"}):
            raise RuntimeError("db down")
        col = store.get(name)
        if col is None:
            col = _Col([])
            store[name] = col
        return col

    if packet is not None and not boom:
        get_collection("paperwork_packets").docs.append(packet)
    if bond is not None and not boom and not bond_boom:
        get_collection("bond_cases").docs.append(bond)
    send = AsyncMock(side_effect=AssertionError("no text"))
    with patch("dashboard.extensions.get_collection", side_effect=get_collection), \
         patch("dashboard.services.swipesimple_invoice_service._http_create_invoice", side_effect=AssertionError("no charge")), \
         patch("dashboard.services.swipesimple_invoice_service._share_invoice_http", side_effect=AssertionError("no charge")), \
         patch("dashboard.services.bb_client.send_message_universal", send), \
         patch("dashboard.services.docuseal_service.DocuSealService.create_submission", side_effect=AssertionError("no send")):
        yield send


def _no_pay(text: str) -> None:
    assert 'data-pay-step="ready"' not in text
    assert "swipesimple.com" not in text
    assert "/links/TEST-done" not in text
    assert "99999.99" not in text


def _no_names(text: str) -> None:
    assert NAME not in text
    assert COSIGNER not in text
    assert DEFENDANT not in text


def _get(path: str):
    return client.get(path)


def test_unsigned_hides_the_pay_step():
    packet = _packet(docuseal_submitters=[
        {"role": "indemnitor", "status": "awaiting", "name": NAME},
        {"role": "coindemnitor", "status": "awaiting", "name": COSIGNER},
        {"role": "defendant", "status": "sent", "name": DEFENDANT},
    ])
    with _db(packet, _bond()):
        response = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor&amount=99999.99")
    assert response.status_code == 200
    _no_pay(response.text)
    _no_names(response.text)
    assert 'data-pay-step="hidden"' in response.text
    assert "<li>Indemnitor</li>" in response.text
    assert "agent will text" not in response.text.lower()


def test_unverified_signer_shows_nothing():
    packet = _packet(docuseal_submitters=[
        {"role": "indemnitor", "status": "unverified", "name": NAME},
        {"role": "coindemnitor", "status": "awaiting", "name": COSIGNER},
    ])
    with _db(_with_scan(packet), _bond()):
        response = _get(f"/api/portal/done?packet_id={PACKET_ID}&role=indemnitor&amount=99999.99")
    assert response.status_code == 200
    assert "This paperwork is closed" in response.text
    assert "nothing else" in response.text
    _no_pay(response.text)
    _no_names(response.text)
    assert "Still needs to sign" not in response.text
    assert "Co-indemnitor" not in response.text


def test_voided_packet_shows_nothing():
    packet = _packet(voided=True, status="voided")
    with _db(_with_scan(packet), _bond()):
        response = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor")
    assert response.status_code == 200
    assert "This paperwork is closed" in response.text
    _no_pay(response.text)
    _no_names(response.text)
    assert 'data-pay-step="ready"' not in response.text


def test_signed_but_unverified_identity_hides_pay_and_lists_roles():
    with _db(_packet(), _bond()):
        response = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor&amount=99999.99")
    assert response.status_code == 200
    _no_pay(response.text)
    _no_names(response.text)
    assert "You finished signing as the Indemnitor." in response.text
    assert "<li>Co-indemnitor</li>" in response.text
    assert "<li>Defendant</li>" in response.text
    assert 'data-pay-step="hidden"' in response.text
    assert "What happens next" in response.text
    assert "(239) 332-2245" in response.text
    assert "(239) 955-0178" in response.text
    assert "Mon-Fri 9-5" in response.text
    assert "24/7" in response.text
    assert "agent will text" not in response.text.lower()
    assert "call us" not in response.text.lower()


def test_verified_signature_shows_the_server_amount():
    pay = _pay_url()
    with _db(_with_scan(_packet()), _bond()) as send:
        response = _get(
            f"/done?packet_id={PACKET_ID}&role=indemnitor"
            "&amount=99999.99&premium=1&payment_link=https://evil.example/pay"
        )
        portal = _get(f"/api/portal/done?packet_id={PACKET_ID}&role=indemnitor&amount=1.00")
    assert response.status_code == 200
    assert portal.status_code == 200
    for text in (response.text, portal.text):
        assert 'data-pay-step="ready"' in text
        assert "500.00" in text
        assert pay in text
        assert "99999.99" not in text
        assert "evil.example" not in text
        assert "1.00" not in text
        assert "You finished signing as the Indemnitor." in text
        assert "<li>Co-indemnitor</li>" in text
        assert "<li>Defendant</li>" in text
        _no_names(text)
        assert "<main" in text
        assert 'name="viewport"' in text
        assert "min-height: 48px" in text
        assert "What happens next" in text
        assert "after release" in text.lower() or "After release" in text
    assert send.await_count == 0
    assert response.headers.get("cache-control") == "no-store"


def test_mismatched_invoice_cents_hide_the_pay_link():
    """A stored link whose cents are not the confirmed down payment is not shown."""
    with _db(_with_scan(_packet()), _bond(swipesimple_invoice_amount_cents=100)):
        response = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor&amount=500.00")
    assert response.status_code == 200
    _no_pay(response.text)
    assert 'data-pay-step="hidden"' in response.text
    assert "500.00" not in response.text


def test_load_failure_is_generic_with_no_pay_link():
    with _db(boom=True):
        failed = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor&amount=99999.99")
    assert failed.status_code == 200
    assert "We could not open this case" in failed.text
    _no_pay(failed.text)
    assert "What happens next" in failed.text
    assert "(239) 332-2245" in failed.text
    assert "Mon-Fri 9-5" in failed.text

    with _db(_with_scan(_packet()), _bond(), bond_boom=True):
        bond_down = _get(f"/done?packet_id={PACKET_ID}&role=indemnitor")
    assert "We could not open this case" in bond_down.text
    _no_pay(bond_down.text)

    missing = _get("/done?amount=99999.99")
    assert "We could not open this case" in missing.text
    _no_pay(missing.text)


def test_done_url_carries_packet_and_role_not_an_amount():
    from dashboard.services.docuseal_signing_ux import paperwork_done_url_for

    url = paperwork_done_url_for(PACKET_ID, "co_indemnitor")
    assert url.endswith(f"/done?packet_id={PACKET_ID}&role=coindemnitor")
    assert "amount" not in url
    assert "500" not in url

    mock_packets = type("P", (), {})()
    mock_packets.find_one = AsyncMock(return_value={
        "packet_id": PACKET_ID,
        "voided": False,
        "status": "pending_signature",
        "defendant_name": DEFENDANT,
        "docuseal_submitters": [
            {"role": "indemnitor", "slug": "ind-test", "status": "pending", "name": NAME},
        ],
    })
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_packets):
        page = client.get(f"/sign/{PACKET_ID}/indemnitor")
    assert page.status_code == 200
    assert f"/done?packet_id={PACKET_ID}&role=indemnitor" in page.text
    assert "amount=" not in page.text
