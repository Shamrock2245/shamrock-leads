"""Data-driven surety onboarding: publish gate, OSI/Palmetto parity, fail closed."""
from __future__ import annotations

import io
import os
from unittest.mock import patch

import fitz
import pdfplumber
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.bond_pdf_service import (
    build_osi_field_values,
    build_palmetto_field_values,
    fill_osi_bond,
    fill_palmetto_bond,
)
from dashboard.services import surety_registry as sr
from dashboard.services import surety_template_store as store
from dashboard.services.surety_canonical import required_rule_ids, validate_publish
from dashboard.services.surety_packet_fill import (
    FAKE_PREVIEW_BOND,
    SuretyDataMissing,
    docuseal_mapped_aliases,
    fill_pdf_bytes,
    fill_published_appearance,
    render_preview,
    resolve_fail_closed_canonical,
)

FIXTURE = {
    "name": "PERKINS, MICHAEL JAMES",
    "booking_number": "1029767",
    "county": "Lee",
    "bond_amount": 5000,
    "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
    "case_number": "26CF016741",
    "court_date": "9/8/2026",
    "court_time": "8:30:00 AM",
    "court_type": "Circuit Court",
    "address": "2424 JACKSON ST FORT MYERS FL 33901",
    "poa_number": "OSI6 20132136",
    "bond_date": "08/07/2026",
    "indemnitor_name": "JANE DOE",
}

REQUIRED_IDS = [
    "defendant.identity",
    "defendant.county",
    "booking.number",
    "case.number",
    "charge.description",
    "charge.bond_amount",
    "charge.poa_number",
    "bond.premium_amount",
    "bond.execution_date",
]


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    store.reset_for_tests(tmp_path)
    yield
    store.reset_for_tests(tmp_path)


def _widgets(pdf: bytes) -> dict:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        found = {}
        for page in doc:
            for widget in page.widgets() or []:
                found[widget.field_name] = widget.field_value or ""
        return found
    finally:
        doc.close()


def _pixmap(pdf: bytes) -> bytes:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        return doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False).tobytes()
    finally:
        doc.close()


def _plumber_text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return "\n".join((page.extract_text() or "") for page in document.pages)


def _xref_v(pdf: bytes, name: str):
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name == name:
                    return doc.xref_get_key(widget.xref, "V")
        return None
    finally:
        doc.close()


def _acroform(pairs) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    for name, value in pairs:
        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(72, y, 420, y + 16)
        widget.field_value = value
        page.add_widget(widget)
        y += 20
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


SUGGESTED_FIELDS = [
    ("defendantNameField", "OLD NAME"),
    ("countyField", "OldCounty"),
    ("ArrestNumberField", "OLD-BOOK"),
    ("CaseNumberField", "OLD-CASE"),
    ("chargesField1", "OLD CHARGE"),
    ("numericBondAmount", "$9.00"),
    ("powerNumField", "OLD-POA"),
    ("calculatedPremiumField", "$9.00"),
    ("dayField", "1"),
    ("monthWrittenField", "January"),
    ("yearYYYYField", "1999"),
]


def test_required_rule_list_is_explicit():
    assert required_rule_ids() == REQUIRED_IDS


def test_osi_and_palmetto_seeds_pass_publish_validation():
    for sid in ("osi", "palmetto"):
        check = validate_publish(store.seed_version(sid))
        assert check["ok"], check["missing"]
        assert check["missing"] == []


def test_publish_blocks_when_required_canonical_is_unmapped():
    draft = store.create_draft(surety_id="lexington", label="Lexington National")
    store.add_form(draft["version_id"], "blank.pdf", _acroform([("Notes", "x")]))
    check = validate_publish(store.get_version(draft["version_id"]))
    assert check["ok"] is False
    for rule in REQUIRED_IDS:
        assert rule in check["missing"] or any(rule in item for item in check["missing"])
    with pytest.raises(store.SuretyTemplateError) as raised:
        store.publish_draft(draft["version_id"], "tester")
    assert raised.value.code == "required_unmapped"


def test_fail_closed_resolver_does_not_invent_money_or_poa():
    values = resolve_fail_closed_canonical({
        "name": "SAMPLE, NOT A PERSON",
        "bond_amount": 5000,
        "county": "Sample",
    })
    assert values["bond.premium_amount"] == ""
    assert values["bond.premium_words"] == ""
    assert values["charge.poa_number"] == ""
    assert values["charge.description"] == ""
    assert values["defendant.phone"] == ""
    assert values["defendant.email"] == ""
    assert values["indemnitor.phone"] == ""
    assert values["court.date"] == ""
    legacy, _fonts = build_osi_field_values({
        "name": "DOE, JANE",
        "bond_amount": 5000,
        "bond_date": "01/02/2020",
    })
    assert legacy["NumericPremiumAmount"] == "$500.00"
    assert legacy["DefCharge1"] == "No Charge Specified"


def _publish_suggested(surety_id="samplecarrier", template_id=""):
    draft = store.create_draft(
        surety_id=surety_id,
        label="Sample Carrier",
        poa_prefixes=[{"prefix": "SMP5", "max_bond_amount": 5000}],
        repeat_per_charge=True,
        docuseal_template_id=template_id,
    )
    store.add_form(draft["version_id"], "packet.pdf", _acroform(SUGGESTED_FIELDS))
    store.update_draft(draft["version_id"], {"use_suggestions": True})
    return store.publish_draft(draft["version_id"], "tester")


def test_publish_activates_surety_and_blocks_missing_premium():
    published = _publish_suggested()
    assert published["immutable"] is True
    assert published["version"] == 1
    assert sr.is_supported_surety("samplecarrier")
    assert sr.is_supported_surety("lexington") is False
    filled = fill_published_appearance("samplecarrier", dict(FAKE_PREVIEW_BOND), strict=True)
    widgets = _widgets(filled)
    assert widgets["defendantNameField"] == "SAMPLE, NOT A PERSON"
    assert widgets["powerNumField"] == "SAMPLE-POA-000"
    assert widgets["calculatedPremiumField"] == "$100.00"
    assert "OLD" not in widgets["defendantNameField"]
    with pytest.raises(SuretyDataMissing) as raised:
        fill_published_appearance("samplecarrier", {
            "name": "SAMPLE, NOT A PERSON",
            "county": "Sample",
            "booking_number": "SAMPLE-BOOKING-000",
            "case_number": "00-SAMPLE-000",
            "charge": "SAMPLE CHARGE ONLY",
            "bond_amount": 1000,
            "poa_number": "SAMPLE-POA-000",
            "bond_date": "01/01/2000",
        })
    assert "bond.premium_amount" in raised.value.missing


def test_empty_fill_clears_template_v():
    pdf = _acroform([("CaseNum", "26MM020844")])
    assert _xref_v(pdf, "CaseNum")[1] != "()"
    cleared = fill_pdf_bytes(pdf, {"CaseNum": ""})
    kind, value = _xref_v(cleared, "CaseNum")
    # PyMuPDF reports a cleared () value as an empty string. The template
    # sample must not come back on save (bond_pdf_service._clear_widget_value).
    assert "26MM" not in str(value)
    assert str(value).strip() in ("", "()", "null") or kind == "null"
    assert (_widgets(cleared).get("CaseNum") or "") == ""

    osi = fill_osi_bond({
        "name": "DOE, JOHN",
        "booking_number": "1029767",
        "case_number": "1029767",
        "bond_amount": 1000,
        "charge": "TEST CHARGE",
        "court_date": "TBN",
        "county": "Lee",
        "poa_number": "OSI3 1",
        "bond_date": "08/07/2026",
    })
    kind, value = _xref_v(osi, "CaseNum")
    assert (_widgets(osi).get("CaseNum") or "") == ""
    assert "26MM020844" not in str(value)
    assert str(value).strip() in ("", "()", "null") or kind == "null"


def test_osi_and_palmetto_published_fill_matches_legacy_path():
    osi_direct = fill_osi_bond(FIXTURE)
    osi_published = fill_published_appearance("osi", FIXTURE)
    assert _widgets(osi_direct) == _widgets(osi_published)
    assert _pixmap(osi_direct) == _pixmap(osi_published)
    assert _plumber_text(osi_direct) == _plumber_text(osi_published)
    osi_widgets = _widgets(osi_published)
    assert osi_widgets["DefLastName"] == "PERKINS"
    assert osi_widgets["DefFirstName"] == "MICHAEL JAMES"
    assert osi_widgets["CaseNum"] == "26CF016741"
    assert osi_widgets["Arrest/case No"] == "1029767"
    assert osi_widgets["NumericPremiumAmount"] == "$500.00"
    assert osi_widgets["BondAmountCharge1"] == "$5,000.00"
    assert osi_widgets["PowerNum"] == "OSI6 20132136"
    assert osi_widgets["DayDD"] == "7"
    assert osi_widgets["Month"] == "August"
    assert osi_widgets["YearYY"] == "26"
    assert "DRUGS" in osi_widgets["DefCharge1"]
    assert build_osi_field_values(FIXTURE)[0]["IndNameandDefName"] == "JANE DOE / PERKINS, MICHAEL JAMES"

    palm_data = dict(FIXTURE)
    palm_data["poa_number"] = "PSC15 2644778"
    palm_direct = fill_palmetto_bond(palm_data)
    palm_published = fill_published_appearance("palmetto", palm_data)
    assert _widgets(palm_direct) == _widgets(palm_published)
    assert _pixmap(palm_direct) == _pixmap(palm_published)
    assert _plumber_text(palm_direct) == _plumber_text(palm_published)
    palm_widgets = _widgets(palm_published)
    assert palm_widgets["defendantNameField"] == "PERKINS, MICHAEL JAMES"
    assert palm_widgets["ArrestNumberField"] == "1029767"
    assert palm_widgets["powerNumField"] == "PSC15 2644778"
    assert palm_widgets["calculatedPremiumField"] == "$500.00"
    assert palm_widgets["numericBondAmount"] == "$5,000.00"
    assert palm_widgets["yearYYYYField"] == "2026"
    assert "9/8/2026" in palm_widgets["CourtDateAndTimeField"]
    # Historical writer keys that are not widget names stay off the page.
    assert "chargesField1" not in palm_widgets
    assert build_palmetto_field_values(palm_data)[0]["chargesField1"].startswith("DRUGS")


def test_docuseal_env_wins_over_published_template_id():
    store.SEEDS["osi"]["docuseal_template_id"] = "999"
    store.SEEDS["palmetto"]["docuseal_template_id"] = "888"
    try:
        with patch.dict(os.environ, {
            "DOCUSEAL_TEMPLATE_ID_OSI": "1",
            "DOCUSEAL_TEMPLATE_ID_PALMETTO": "5",
            "DOCUSEAL_TEMPLATE_ID": "",
        }):
            assert sr.template_id_for("osi") == "1"
            assert sr.template_id_for("palmetto") == "5"
        from dashboard.services.docuseal_service import resolve_template_id_for_surety
        with patch.dict(os.environ, {
            "DOCUSEAL_TEMPLATE_ID_OSI": "1",
            "DOCUSEAL_TEMPLATE_ID_PALMETTO": "5",
        }):
            assert resolve_template_id_for_surety("osi") == "1"
            assert resolve_template_id_for_surety("palmetto") == "5"
        with patch.dict(os.environ, {
            "DOCUSEAL_TEMPLATE_ID_OSI": "",
            "DOCUSEAL_TEMPLATE_ID": "",
            "DOCUSEAL_TEMPLATE_ID_PALMETTO": "",
        }):
            assert sr.template_id_for("osi") == "999"
            assert sr.template_id_for("palmetto") == "888"
    finally:
        store.SEEDS["osi"]["docuseal_template_id"] = ""
        store.SEEDS["palmetto"]["docuseal_template_id"] = ""

    published = _publish_suggested(template_id="42")
    assert published["docuseal_field_mode"] == "mapped"
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DOCUSEAL_TEMPLATE_ID_SAMPLECARRIER", None)
        assert sr.template_id_for("samplecarrier") == "42"
        assert sr.is_supported_surety("lexington") is False
    aliases = docuseal_mapped_aliases("osi", FIXTURE)
    assert aliases == {}
    mapped = docuseal_mapped_aliases("samplecarrier", dict(FAKE_PREVIEW_BOND))
    assert mapped["defendantNameField"] == "SAMPLE, NOT A PERSON"
    assert "" not in mapped.values()


def test_preview_is_local_sample_and_repeats_per_charge():
    published = _publish_suggested()
    pdf = render_preview(published)
    assert pdf[:4] == b"%PDF"
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert doc.page_count == 2
        values = []
        for page in doc:
            for widget in page.widgets() or []:
                base = str(widget.field_name or "").split(" [", 1)[0]
                if base == "powerNumField":
                    values.append(widget.field_value)
        assert values == ["SAMPLE-POA-000", "SAMPLE-POA-001"]
    finally:
        doc.close()
    text = _plumber_text(pdf)
    assert "SAMPLE" in text or "SAMPLE-POA-000" in "".join(_widgets(pdf).values())


def test_onboarding_routes_are_staff_gated(monkeypatch):
    from dashboard.routers.surety_onboarding import surety_onboarding_bp

    monkeypatch.setenv("DASHBOARD_PIN", "test-pin-not-real")
    app = FastAPI()
    app.include_router(surety_onboarding_bp)
    client = TestClient(app)

    denied = client.get("/api/crm/sureties/onboarding/catalog")
    assert denied.status_code == 401

    with patch(
        "dashboard.routers.surety_onboarding.get_session_from_request",
        return_value={"auth": True, "role": "recovery", "email": "recovery@example.com"},
    ):
        recovery = client.get("/api/crm/sureties/onboarding/catalog")
    assert recovery.status_code == 401

    with patch(
        "dashboard.routers.surety_onboarding.get_session_from_request",
        return_value={"auth": True, "role": "sub_agent", "email": "agent@example.com"},
    ):
        sub = client.get("/api/crm/sureties/onboarding")
    assert sub.status_code == 401

    headers = {"X-Admin-Token": "test-pin-not-real"}
    catalog = client.get("/api/crm/sureties/onboarding/catalog", headers=headers)
    assert catalog.status_code == 200
    assert "charge.poa_number" in catalog.json()["required"]

    created = client.post(
        "/api/crm/sureties/onboarding/drafts",
        headers=headers,
        json={"surety_id": "lexington", "label": "Lexington National", "poa_prefixes": []},
    )
    assert created.status_code == 200
    version_id = created.json()["version"]["version_id"]
    uploaded = client.post(
        f"/api/crm/sureties/onboarding/drafts/{version_id}/forms",
        headers=headers,
        files={"file": ("form.pdf", _acroform([("Notes", "x")]), "application/pdf")},
    )
    assert uploaded.status_code == 200
    assert "storage_path" not in uploaded.json()["version"]["forms"][0]
    blocked = client.post(
        f"/api/crm/sureties/onboarding/drafts/{version_id}/publish",
        headers=headers,
    )
    assert blocked.status_code == 422
    assert "charge.poa_number" in blocked.json()["missing"]

    seeded = client.post(
        "/api/crm/sureties/onboarding/drafts/seed-osi-v1/publish",
        headers=headers,
    )
    assert seeded.status_code == 409
