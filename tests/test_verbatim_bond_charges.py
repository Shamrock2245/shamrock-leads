"""Charge text on bond paperwork is verbatim and complete.

These cases fail on main: finalize drops request ``charge_details``, more than
three charges become the first three plus `` (see case file)``, and rows past
the printed grid are discarded. Synthetic TEST data only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import fitz
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.bond_pdf_service import generate_appearance_bonds
from dashboard.services.charge_verbatim import (
    DOCUSEAL_CHARGE_ROW_CAPACITY,
    ChargeCapacityError,
    appearance_box_clips,
    place_verbatim_charges,
    resolve_verbatim_charge_rows,
)
from dashboard.services.docuseal_service import DocuSealService, build_bond_data_from_dashboard
from dashboard.services.packet_builder_service import charge_details_from_sources
from dashboard.services.staff_test_case import DEFAULT_SIGNER_EMAIL

# Apostrophe, em dash, and section sign. Statute and degree are separate
# fields so they are not folded into the charge text.
CHARGE_TEXT = [
    "Grand theft — occupant's dwelling",
    "Burglary of a conveyance",
    "Battery — domestic (simple)",
    "Possession of a firearm",
    "Resisting an officer without violence",
    "Petit theft — occupant's purse",
    "Criminal mischief — $1,000 or more",
    "Trespass in an occupant's structure",
]
STATUTE = "Fla. Stat. § 812.014(2)(c)"
DEGREE = "2nd degree"
SEE_CASE_FILE = "(see case file)"


def _rows(count: int) -> list:
    rows = []
    for index in range(count):
        rows.append({
            "charge": CHARGE_TEXT[index],
            "statute": STATUTE,
            "degree": DEGREE,
            "bond_amount": 1000 * (index + 1),
            "case_number": f"TEST-CASE-{index + 1}",
            "poa_number": f"TEST-POA-{index + 1}",
        })
    return rows


def _join(count: int) -> str:
    return ", ".join(CHARGE_TEXT[:count])


def _prefill(count: int, *, surety_id: str, addendum=True):
    bond = {
        "surety_id": surety_id,
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "county": "Lee",
        "case_number": "TEST-CASE-1",
        "poa_number": "TEST-POA-1",
        "bond_amount": 5000,
        "charge_details": _rows(count),
        "charges": "PLAIN STRING MUST NOT WIN",
        "allow_charge_addendum": addendum,
    }
    values = DocuSealService.prefill_values_from_bond(bond)
    return bond, values


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
@pytest.mark.parametrize("count", [2, 3, 4, 8])
def test_charge_text_is_byte_identical_for_each_count(surety_id, count):
    bond, values = _prefill(count, surety_id=surety_id)
    blob = json.dumps(values, ensure_ascii=False)
    assert SEE_CASE_FILE not in blob
    assert values["charges"] == _join(count)
    assert values["charges_summary"] == _join(count)
    assert values["charges"] == values["charges_summary"]

    for index in range(min(count, DOCUSEAL_CHARGE_ROW_CAPACITY)):
        text = CHARGE_TEXT[index]
        slot = index + 1
        assert values[f"offense_{slot}"] == text
        assert values[f"charge_{slot}"] == text
        assert values[f"statute_{slot}"] == STATUTE
        assert values[f"degree_{slot}"] == DEGREE
        assert values[f"case_number_{slot}"] == f"TEST-CASE-{slot}"
        assert "§" in values[f"statute_{slot}"]
        assert "—" in text or "'" in text or "§" in STATUTE

    if count > DOCUSEAL_CHARGE_ROW_CAPACITY:
        overflow = count - DOCUSEAL_CHARGE_ROW_CAPACITY
        for offset in range(overflow):
            text = CHARGE_TEXT[DOCUSEAL_CHARGE_ROW_CAPACITY + offset]
            slot = offset + 1
            assert values[f"charge_addendum_{slot}"] == text
            assert values[f"offense_addendum_{slot}"] == text
            assert values[f"statute_addendum_{slot}"] == STATUTE
            assert values[f"degree_addendum_{slot}"] == DEGREE
            assert values[f"case_addendum_{slot}"] == f"TEST-CASE-{DOCUSEAL_CHARGE_ROW_CAPACITY + slot}"
            assert values[f"bond_addendum_{slot}"] == str(1000 * (DOCUSEAL_CHARGE_ROW_CAPACITY + slot))
        assert f"charge_addendum_{overflow + 1}" not in values

    if appearance_box_clips(surety_id, values["charges_summary"]):
        notes = " ".join(place_verbatim_charges(
            resolve_verbatim_charge_rows(bond),
            capacity=DOCUSEAL_CHARGE_ROW_CAPACITY,
            addendum=True,
            surety_id=surety_id,
            template="docuseal",
        ).layout_notes)
        assert "not shortened" in notes
        assert values["charges_summary"] == _join(count)


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_overflow_addendum_pages_keep_order(surety_id):
    rows = resolve_verbatim_charge_rows({
        "surety_id": surety_id,
        "charge_details": _rows(8),
    })
    placement = place_verbatim_charges(
        rows,
        capacity=DOCUSEAL_CHARGE_ROW_CAPACITY,
        addendum=True,
        surety_id=surety_id,
        template="docuseal",
    )
    assert [row.charge for row in placement.on_form] == CHARGE_TEXT[:4]
    assert [row.charge for row in placement.overflow] == CHARGE_TEXT[4:]
    assert SEE_CASE_FILE not in placement.summary

    doc = fitz.open(stream=placement.addendum_pdf, filetype="pdf")
    assert doc.page_count == 4
    for page_index, expected in enumerate(CHARGE_TEXT[4:]):
        widgets = {w.field_name: w.field_value for w in doc[page_index].widgets()}
        assert widgets[f"charge_addendum_{page_index + 1}"] == expected
        assert widgets[f"statute_addendum_{page_index + 1}"] == STATUTE
        assert widgets[f"degree_addendum_{page_index + 1}"] == DEGREE
        assert "§" in widgets[f"statute_addendum_{page_index + 1}"]
    assert "—" in CHARGE_TEXT[5]
    assert "'" in CHARGE_TEXT[0]
    doc.close()

    data = {
        "defendant_name": "Sample Party One",
        "county": "Lee",
        "bond_amount": 1000,
        "bond_date": "01/15/2026",
        "surety": surety_id,
        "charge_details": _rows(8),
    }
    pdfs = generate_appearance_bonds(data, template=surety_id)
    assert len(pdfs) == 8
    charge_field = "DefCharge1" if surety_id == "osi" else "chargestField1"
    for index, pdf in enumerate(pdfs):
        form = fitz.open(stream=pdf, filetype="pdf")
        widgets = {w.field_name: w.field_value for w in form[0].widgets()}
        assert widgets.get(charge_field) == CHARGE_TEXT[index]
        form.close()


def test_fail_closed_when_template_has_no_addendum():
    rows = resolve_verbatim_charge_rows({"charge_details": _rows(8)})
    with pytest.raises(ChargeCapacityError) as exc:
        place_verbatim_charges(
            rows,
            capacity=DOCUSEAL_CHARGE_ROW_CAPACITY,
            addendum=False,
            surety_id="osi",
            template="docuseal",
        )
    err = exc.value
    assert err.charge_count == 8
    assert err.capacity == 4
    assert "8" in str(err) and "4" in str(err)
    assert SEE_CASE_FILE not in str(err)

    with pytest.raises(ChargeCapacityError) as palmetto:
        place_verbatim_charges(
            rows,
            capacity=DOCUSEAL_CHARGE_ROW_CAPACITY,
            addendum=False,
            surety_id="palmetto",
            template="docuseal",
        )
    assert palmetto.value.charge_count == 8
    assert palmetto.value.capacity == 4


def test_non_ascii_is_not_rewritten():
    text = "Grand theft — occupant's dwelling"
    bond = {
        "surety_id": "osi",
        "defendant_name": "Sample Party One",
        "county": "Lee",
        "charge_details": [{
            "charge": f"  {text}  ",
            "statute": f"  {STATUTE}  ",
            "degree": f"  {DEGREE}  ",
            "case_number": "  TEST-CASE-1  ",
            "bond_amount": "  1500  ",
        }],
    }
    values = DocuSealService.prefill_values_from_bond(bond)
    # Edge strip only. The apostrophe, em dash, and section sign stay.
    assert values["offense_1"] == text
    assert values["charge_1"] == text
    assert values["statute_1"] == STATUTE
    assert values["degree_1"] == DEGREE
    assert values["case_number_1"] == "TEST-CASE-1"
    assert values["charges"] == text
    assert "—" in values["offense_1"]
    assert "'" in values["offense_1"]
    assert "§" in values["statute_1"]
    assert values["offense_1"].encode("utf-8") == text.encode("utf-8")


def test_internal_whitespace_is_kept():
    text = "Battery  —  occupant's  dwelling"
    values = DocuSealService.prefill_values_from_bond({
        "defendant_name": "Sample Party One",
        "county": "Lee",
        "charge_details": [{"charge": text}],
    })
    assert values["offense_1"] == text
    assert values["charges_summary"] == text


def test_stored_rows_are_not_capped():
    stored = [{"charge": CHARGE_TEXT[index % 8], "statute": STATUTE} for index in range(9)]
    rows = charge_details_from_sources(arrest={"charge_details": stored})
    assert len(rows) == 9
    assert rows[8]["charge"] == CHARGE_TEXT[0]
    assert rows[0]["statute"] == STATUTE


def test_request_body_beats_stored_charges_string():
    """Body charge_details win. Context charge_details win when the body has none."""
    body_wins = build_bond_data_from_dashboard(
        ctx={
            "charges": "PLAIN STRING MUST NOT WIN",
            "charge_details": [{"charge": "STORED ROW"}],
            "county": "Lee",
            "defendant": {"name": "Sample Party One"},
            "indemnitor": {"name": "Sample Party Two"},
        },
        body={"charge_details": _rows(2)},
        surety_id="palmetto",
    )
    values = DocuSealService.prefill_values_from_bond(body_wins)
    assert values["offense_1"] == CHARGE_TEXT[0]
    assert values["offense_2"] == CHARGE_TEXT[1]
    assert "PLAIN STRING" not in values["charges"]
    assert "STORED ROW" not in values["charges"]

    stored_wins = build_bond_data_from_dashboard(
        ctx={
            "charges": "PLAIN STRING MUST NOT WIN",
            "charge_details": [{
                "charge": CHARGE_TEXT[0],
                "statute": STATUTE,
                "degree": DEGREE,
                "case_number": "TEST-CASE-9",
            }],
            "county": "Lee",
            "defendant": {"name": "Sample Party One"},
            "indemnitor": {"name": "Sample Party Two"},
        },
        body={},
        surety_id="osi",
    )
    stored_values = DocuSealService.prefill_values_from_bond(stored_wins)
    assert stored_values["offense_1"] == CHARGE_TEXT[0]
    assert stored_values["statute_1"] == STATUTE
    assert stored_values["case_number_1"] == "TEST-CASE-9"
    assert "PLAIN STRING" not in stored_values["charges"]


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        frozen = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        if tz is None:
            return frozen.replace(tzinfo=None)
        return frozen.astimezone(tz)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _Store:
    def __init__(self):
        self.docs = []
        self.inserts = []

    async def find_one(self, query, projection=None):
        return None

    def find(self, query, projection=None):
        return _Cursor([])

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)

    async def update_one(self, query, update, upsert=False):
        return type("R", (), {"matched_count": 1})()


def _finalize_client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr("dashboard.services.docuseal_service.datetime", _FrozenDateTime)

    captured = []

    async def _request(self, method, path, *, json=None, params=None):
        captured.append({"method": str(method).upper(), "path": path, "json": json})
        if str(method).upper() == "POST" and str(path).rstrip("/").endswith("/submissions"):
            return [{
                "id": 1,
                "submission_id": 44,
                "role": "bondsman",
                "slug": "bondsman-stub",
                "email": DEFAULT_SIGNER_EMAIL,
            }]
        raise RuntimeError(f"Unexpected DocuSeal call {method} {path}")

    svc = DocuSealService(base_url="https://sign.example.invalid", api_key="test-not-a-real-key")
    svc._request = _request.__get__(svc, DocuSealService)

    async def _blocked(self, method, url, *args, **kwargs):
        raise RuntimeError("DocuSeal network is blocked in this test")

    monkeypatch.setattr(httpx.AsyncClient, "request", _blocked)

    stores = {}

    def collections(name):
        if name not in stores:
            stores[name] = _Store()
        return stores[name]

    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)
    token = _sign_token(
        email="office@example.invalid",
        role="god_admin",
        agent_name=None,
        license_number=None,
        is_admin=True,
    )
    client.cookies.update({COOKIE_NAME: token})
    return client, captured, stores, collections, svc


def _post_finalize(monkeypatch, body):
    client, captured, stores, collections, svc = _finalize_client(monkeypatch)
    with patch(
        "dashboard.services.packet_builder_service.resolve_client_esign_provider",
        new=AsyncMock(return_value="docuseal"),
    ), patch(
        "dashboard.services.staff_chain_service.ensure_match_bondcase",
        new=AsyncMock(),
    ), patch(
        "dashboard.routers.paperwork.get_collection",
        side_effect=collections,
    ), patch(
        "dashboard.extensions.get_collection",
        side_effect=collections,
    ), patch(
        "dashboard.services.docuseal_service.get_docuseal_service",
        return_value=svc,
    ), patch(
        "dashboard.services.docuseal_initial_delivery.deliver_initial_docuseal_links",
        new=AsyncMock(),
    ), patch(
        "dashboard.routers.paperwork._finalize_auto_payment_link",
        new=AsyncMock(),
    ):
        response = client.post("/api/paperwork/packet/finalize", json=body)
    return response, captured, stores


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_finalize_uses_request_charge_details(monkeypatch, surety_id):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-CHARGES-{surety_id.upper()}",
        "case_number": "TEST-CASE-1",
        "packet_id": f"PKT-TEST-CHARGES-{surety_id.upper()}",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": _rows(4),
    })
    assert response.status_code == 200, response.text
    posts = [row for row in captured if row["method"] == "POST"]
    assert len(posts) == 1
    payload = posts[0]["json"]
    fields = {
        field["name"]: field.get("default_value")
        for field in (payload["submitters"][0].get("fields") or [])
    }
    assert fields["offense_1"] == CHARGE_TEXT[0]
    assert fields["offense_4"] == CHARGE_TEXT[3]
    assert fields["statute_1"] == STATUTE
    assert fields["degree_2"] == DEGREE
    assert fields["case_number_3"] == "TEST-CASE-3"
    assert fields["charges"] == _join(4)
    assert fields["charges_summary"] == _join(4)
    assert SEE_CASE_FILE not in json.dumps(fields, ensure_ascii=False)
    assert "PLAIN STRING" not in fields["charges"]
    assert "§" in fields["statute_4"]
    packet = stores["paperwork_packets"].inserts[0]
    assert packet.get("charge_addendum_pdf", b"") in (b"", None)


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_finalize_fail_closed_without_addendum(monkeypatch, surety_id):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-OVERFLOW-{surety_id.upper()}",
        "case_number": "TEST-CASE-1",
        "packet_id": f"PKT-TEST-OVERFLOW-{surety_id.upper()}",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 8000,
        "county": "Lee",
        "state": "FL",
        "charge_details": _rows(8),
        "allow_charge_addendum": False,
    })
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "charge_capacity_exceeded"
    assert body["charge_count"] == 8
    assert body["capacity"] == 4
    assert "8" in body["message"] and "4" in body["message"]
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_finalize_eight_charges_land_on_addendum(monkeypatch, surety_id):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-EIGHT-{surety_id.upper()}",
        "case_number": "TEST-CASE-1",
        "packet_id": f"PKT-TEST-EIGHT-{surety_id.upper()}",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 8000,
        "county": "Lee",
        "state": "FL",
        "charge_details": _rows(8),
    })
    assert response.status_code == 200, response.text
    payload = [row for row in captured if row["method"] == "POST"][0]["json"]
    fields = {
        field["name"]: field.get("default_value")
        for field in (payload["submitters"][0].get("fields") or [])
    }
    assert fields["charges"] == _join(8)
    assert fields["offense_4"] == CHARGE_TEXT[3]
    assert fields["charge_addendum_1"] == CHARGE_TEXT[4]
    assert fields["charge_addendum_4"] == CHARGE_TEXT[7]
    assert SEE_CASE_FILE not in json.dumps(fields, ensure_ascii=False)
    packet = stores["paperwork_packets"].inserts[0]
    pdf = packet["charge_addendum_pdf"]
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.page_count == 4
    widgets = {w.field_name: w.field_value for w in doc[0].widgets()}
    assert widgets["charge_addendum_1"] == CHARGE_TEXT[4]
    last = {w.field_name: w.field_value for w in doc[3].widgets()}
    assert last["charge_addendum_4"] == CHARGE_TEXT[7]
    doc.close()
