"""Charge text on bond paperwork is verbatim, and overflow fails closed.

These cases fail on main: finalize drops request ``charge_details``, more than
three charges become the first three plus `` (see case file)``, and a comma
inside one charge is treated as a second charge. Synthetic TEST data only.

DocuSeal ``create_submission`` posts ``template_id`` and ``submitters``. It
does not attach an extra PDF. Template 1 prints ``offense_1``..``offense_4``
plus ``charges_summary``. Template 5 prints ``charges_summary`` only. A charge
that cannot land on one of those fields returns 422 ``charge_capacity_exceeded``.
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
    ChargeCapacityError,
    addendum_allowed,
    fit_charges_for_template,
    is_charge_payload_key,
    resolve_verbatim_charge_rows,
    split_charges_text,
    summary_box_clips,
    summary_character_capacity,
    template_field_names,
    template_record,
)
from dashboard.services.docuseal_service import DocuSealService, build_bond_data_from_dashboard
from dashboard.services.packet_builder_service import charge_details_from_sources
from dashboard.services.staff_test_case import DEFAULT_SIGNER_EMAIL

# Apostrophe and em dash stay in the charge text. Statute and degree stay on
# the stored row. Neither template has statute_N or degree_N fields.
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

# Smallest charges_summary box on each live template, at the 5.5pt floor.
OSI_SUMMARY_CHARS = 87
PALMETTO_SUMMARY_CHARS = 125
OSI_OFFENSE_ROWS = 4

_SUBMISSION_KEYS = {
    "template_id",
    "send_email",
    "order",
    "submitters",
    "send_sms",
    "message",
    "completed_redirect_url",
    "variables",
    "expire_at",
}


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


def _bond(count: int, *, surety_id: str) -> dict:
    return {
        "surety_id": surety_id,
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "county": "Lee",
        "case_number": "TEST-CASE-1",
        "poa_number": "TEST-POA-1",
        "bond_amount": 5000,
        "charge_details": _rows(count),
        "charges": "PLAIN STRING MUST NOT WIN",
        "allow_charge_addendum": True,
    }


def _prefill(count: int, *, surety_id: str):
    bond = _bond(count, surety_id=surety_id)
    values = DocuSealService.prefill_values_from_bond(bond)
    return bond, values


def _charge_keys(values: dict) -> list:
    return [key for key in values if is_charge_payload_key(str(key))]


def test_live_inventory_counts_and_charge_fields():
    osi = template_record("osi")
    palmetto = template_record("palmetto")
    assert osi["template_id"] == 1
    assert palmetto["template_id"] == 5
    assert osi["unique_field_names_including_blank"] == 228
    assert palmetto["unique_field_names_including_blank"] == 157
    assert osi["blank_field_name_count"] == 3
    assert palmetto["blank_field_name_count"] == 2
    assert osi["offense_rows"] == [f"offense_{n}" for n in range(1, 5)]
    assert palmetto["offense_rows"] == []
    assert osi["charge_text_fields"] == ["charges_summary", "offense_1", "offense_2", "offense_3", "offense_4"]
    assert palmetto["charge_text_fields"] == ["charges_summary"]
    assert summary_character_capacity("osi") == OSI_SUMMARY_CHARS
    assert summary_character_capacity("palmetto") == PALMETTO_SUMMARY_CHARS
    for surety in ("osi", "palmetto"):
        names = template_field_names(surety)
        assert "charges_summary" in names
        assert "charges" not in names
        assert "defendant_prior_offense" not in osi["charge_text_fields"]
        for name in names:
            assert "addendum" not in name
            assert not name.startswith("statute_")
            assert not name.startswith("degree_")
    assert "defendant_prior_offense" in template_field_names("palmetto")
    assert addendum_allowed({"allow_charge_addendum": True}) is False


@pytest.mark.parametrize("count", [2, 3, 4])
def test_osi_charge_text_is_byte_identical(count):
    bond, values = _prefill(count, surety_id="osi")
    blob = json.dumps(values, ensure_ascii=False)
    assert SEE_CASE_FILE not in blob
    summary = _join(count)
    assert values["charges_summary"] == summary
    assert "charges" not in values
    assert "PLAIN STRING" not in values["charges_summary"]
    for index in range(count):
        slot = index + 1
        text = CHARGE_TEXT[index]
        assert values[f"offense_{slot}"] == text
        assert values[f"offense_{slot}"].encode("utf-8") == text.encode("utf-8")
        assert values[f"case_number_{slot}"] == f"TEST-CASE-{slot}"
        assert f"charge_{slot}" not in values
        assert f"statute_{slot}" not in values
        assert f"degree_{slot}" not in values
    assert "—" in values["charges_summary"]
    assert "'" in values["charges_summary"]
    assert f"offense_{count + 1}" not in values
    for key in _charge_keys(values):
        assert key in template_field_names("osi")
    rows = resolve_verbatim_charge_rows(bond)
    assert rows[0].statute == STATUTE
    assert rows[0].degree == DEGREE
    if summary_box_clips("osi", summary):
        notes = " ".join(fit_charges_for_template(rows, surety_id="osi").layout_notes)
        assert "not shortened" in notes
        assert values["charges_summary"] == summary


@pytest.mark.parametrize("count", [5, 8])
def test_osi_fails_closed_above_four_offense_rows(count):
    rows = resolve_verbatim_charge_rows(_bond(count, surety_id="osi"))
    with pytest.raises(ChargeCapacityError) as exc:
        fit_charges_for_template(rows, surety_id="osi")
    err = exc.value
    assert err.charge_count == count
    assert err.capacity == OSI_OFFENSE_ROWS
    assert err.unit == "rows"
    assert str(count) in str(err) and "4" in str(err)
    assert SEE_CASE_FILE not in str(err)
    with pytest.raises(ChargeCapacityError):
        DocuSealService.prefill_values_from_bond(_bond(count, surety_id="osi"))


def test_palmetto_summary_is_byte_identical_when_it_fits():
    # Four of the fixture charges join to 113 characters. The defendant
    # application box holds 125, so the summary prints and nothing else does.
    assert len(_join(4)) == 113
    assert len(_join(4)) <= PALMETTO_SUMMARY_CHARS
    bond, values = _prefill(4, surety_id="palmetto")
    assert values["charges_summary"] == _join(4)
    assert values["charges_summary"].encode("utf-8") == _join(4).encode("utf-8")
    assert "—" in values["charges_summary"]
    assert "'" in values["charges_summary"]
    assert SEE_CASE_FILE not in values["charges_summary"]
    assert "PLAIN STRING" not in values["charges_summary"]
    assert "charges" not in values
    for key in values:
        assert not str(key).startswith("offense_")
        assert not str(key).startswith("charge_")
        assert "addendum" not in str(key)
        assert not str(key).startswith("statute_")
    for key in _charge_keys(values):
        assert key in template_field_names("palmetto")
    rows = resolve_verbatim_charge_rows(bond)
    assert [row.charge for row in rows] == CHARGE_TEXT[:4]
    assert rows[0].statute == STATUTE
    placement = fit_charges_for_template(rows, surety_id="palmetto")
    assert placement.on_form == []
    assert placement.addendum_pdf == b""
    assert placement.addendum is False
    assert placement.summary == _join(4)


@pytest.mark.parametrize("count", [5, 8])
def test_palmetto_summary_fails_closed_when_the_box_cannot_show_it(count):
    summary = _join(count)
    assert len(summary) > PALMETTO_SUMMARY_CHARS
    rows = resolve_verbatim_charge_rows(_bond(count, surety_id="palmetto"))
    with pytest.raises(ChargeCapacityError) as exc:
        fit_charges_for_template(rows, surety_id="palmetto")
    err = exc.value
    assert err.unit == "characters"
    assert err.capacity == PALMETTO_SUMMARY_CHARS
    assert err.charge_count == count
    assert err.summary_characters == len(summary)
    assert str(count) in str(err) and str(PALMETTO_SUMMARY_CHARS) in str(err)
    with pytest.raises(ChargeCapacityError):
        DocuSealService.prefill_values_from_bond(_bond(count, surety_id="palmetto"))


def test_palmetto_character_threshold_is_the_smallest_box():
    short = "x" * PALMETTO_SUMMARY_CHARS
    long = "x" * (PALMETTO_SUMMARY_CHARS + 1)
    assert summary_box_clips("palmetto", short) is False
    assert summary_box_clips("palmetto", long) is True
    fit_charges_for_template(
        resolve_verbatim_charge_rows({"surety_id": "palmetto", "charges": short}),
        surety_id="palmetto",
    )
    with pytest.raises(ChargeCapacityError) as exc:
        fit_charges_for_template(
            resolve_verbatim_charge_rows({"surety_id": "palmetto", "charges": long}),
            surety_id="palmetto",
        )
    assert exc.value.capacity == PALMETTO_SUMMARY_CHARS
    assert exc.value.unit == "characters"
    assert exc.value.charge_count == 1


def test_osi_summary_clip_is_a_layout_note():
    """An offense row still carries the charge when the summary box clips."""
    text = "x" * 200
    values = DocuSealService.prefill_values_from_bond({
        "surety_id": "osi",
        "defendant_name": "Sample Party One",
        "county": "Lee",
        "charge_details": [{"charge": text, "case_number": "TEST-CASE-1"}],
    })
    assert values["offense_1"] == text
    assert values["charges_summary"] == text
    notes = " ".join(fit_charges_for_template(
        resolve_verbatim_charge_rows({"surety_id": "osi", "charges": text}),
        surety_id="osi",
    ).layout_notes)
    assert "not shortened" in notes
    assert summary_box_clips("osi", text) is True


def test_comma_in_a_charge_is_not_a_delimiter():
    text = "BATTERY, DOMESTIC"
    assert split_charges_text(text) == [text]
    assert split_charges_text("BATTERY | RESIST OFFICER") == ["BATTERY", "RESIST OFFICER"]
    values = DocuSealService.prefill_values_from_bond({
        "surety_id": "osi",
        "defendant_name": "Sample Party One",
        "county": "Lee",
        "charges": text,
    })
    assert values["offense_1"] == text
    assert values["charges_summary"] == text
    assert "offense_2" not in values


def test_charge_fields_on_the_submission_exist_on_the_live_template():
    for surety in ("osi", "palmetto"):
        count = 2 if surety == "osi" else 4
        _, values = _prefill(count, surety_id=surety)
        names = template_field_names(surety)
        for key in _charge_keys(values):
            assert key in names, key
        blob = " ".join(_charge_keys(values))
        assert "statute_" not in blob
        assert "degree_" not in blob
        assert "addendum" not in blob
        assert "charges_summary" in values


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
    assert values["offense_1"] == text
    assert "charge_1" not in values
    assert "statute_1" not in values
    assert "degree_1" not in values
    assert values["case_number_1"] == "TEST-CASE-1"
    assert values["charges_summary"] == text
    assert "—" in values["offense_1"]
    assert "'" in values["offense_1"]
    assert values["offense_1"].encode("utf-8") == text.encode("utf-8")
    rows = resolve_verbatim_charge_rows(bond)
    assert rows[0].statute == STATUTE
    assert "§" in rows[0].statute
    assert rows[0].degree == DEGREE


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
    assert values["charges_summary"] == _join(2)
    assert "offense_1" not in values
    assert "PLAIN STRING" not in values["charges_summary"]
    assert "STORED ROW" not in values["charges_summary"]
    assert resolve_verbatim_charge_rows(body_wins)[0].statute == STATUTE

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
    assert "statute_1" not in stored_values
    assert stored_values["case_number_1"] == "TEST-CASE-9"
    assert "PLAIN STRING" not in stored_values["charges_summary"]
    assert resolve_verbatim_charge_rows(stored_wins)[0].statute == STATUTE


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


def _submission_fields(captured):
    posts = [row for row in captured if row["method"] == "POST"]
    assert len(posts) == 1
    payload = posts[0]["json"]
    assert set(payload) <= _SUBMISSION_KEYS
    assert "documents" not in payload
    fields = {
        field["name"]: field.get("default_value")
        for field in (payload["submitters"][0].get("fields") or [])
    }
    return payload, fields


def test_finalize_osi_uses_request_charge_details(monkeypatch):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": "osi",
        "booking_number": "TEST-CHARGES-OSI",
        "case_number": "TEST-CASE-1",
        "packet_id": "PKT-TEST-CHARGES-OSI",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": _rows(4),
        "allow_charge_addendum": True,
    })
    assert response.status_code == 200, response.text
    payload, fields = _submission_fields(captured)
    assert payload["template_id"] == 1
    assert fields["offense_1"] == CHARGE_TEXT[0]
    assert fields["offense_4"] == CHARGE_TEXT[3]
    assert fields["case_number_3"] == "TEST-CASE-3"
    assert fields["charges_summary"] == _join(4)
    assert "charges" not in fields
    assert "statute_1" not in fields
    assert "degree_2" not in fields
    assert "charge_addendum_1" not in fields
    assert SEE_CASE_FILE not in json.dumps(fields, ensure_ascii=False)
    assert "PLAIN STRING" not in fields["charges_summary"]
    for key in _charge_keys(fields):
        assert key in template_field_names("osi")
    packet = stores["paperwork_packets"].inserts[0]
    assert "charge_addendum_pdf" not in packet
    assert "not shortened" in " ".join(packet.get("charge_layout_notes") or [])


def test_finalize_palmetto_summary_carries_fitting_charges(monkeypatch):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": "palmetto",
        "booking_number": "TEST-CHARGES-PALMETTO",
        "case_number": "TEST-CASE-1",
        "packet_id": "PKT-TEST-CHARGES-PALMETTO",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": _rows(4),
    })
    assert response.status_code == 200, response.text
    payload, fields = _submission_fields(captured)
    assert payload["template_id"] == 5
    assert fields["charges_summary"] == _join(4)
    assert "offense_1" not in fields
    assert "charges" not in fields
    assert "charge_addendum_1" not in fields
    for key in _charge_keys(fields):
        assert key in template_field_names("palmetto")
    packet = stores["paperwork_packets"].inserts[0]
    assert "charge_addendum_pdf" not in packet


@pytest.mark.parametrize(
    "surety_id,count,unit,capacity",
    [
        ("osi", 5, "rows", 4),
        ("osi", 8, "rows", 4),
        ("palmetto", 5, "characters", PALMETTO_SUMMARY_CHARS),
        ("palmetto", 8, "characters", PALMETTO_SUMMARY_CHARS),
    ],
)
def test_finalize_fails_closed(monkeypatch, surety_id, count, unit, capacity):
    response, captured, stores = _post_finalize(monkeypatch, {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-OVERFLOW-{surety_id.upper()}-{count}",
        "case_number": "TEST-CASE-1",
        "packet_id": f"PKT-TEST-OVERFLOW-{surety_id.upper()}-{count}",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 8000,
        "county": "Lee",
        "state": "FL",
        "charge_details": _rows(count),
        "allow_charge_addendum": True,
    })
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "charge_capacity_exceeded"
    assert body["charge_count"] == count
    assert body["capacity"] == capacity
    assert body["capacity_unit"] == unit
    assert str(count) in body["message"] and str(capacity) in body["message"]
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_appearance_bonds_print_each_charge_in_order(surety_id):
    """Local appearance forms are one PDF per charge. They are not the DocuSeal packet."""
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
    with pytest.raises(ChargeCapacityError):
        fit_charges_for_template(
            resolve_verbatim_charge_rows({"surety_id": surety_id, "charge_details": _rows(8)}),
            surety_id=surety_id,
        )
