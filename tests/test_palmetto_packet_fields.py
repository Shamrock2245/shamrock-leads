"""Palmetto packet widgets land on the carrier blanks and keep the real agent."""
from __future__ import annotations

import io

import fitz
import pdfplumber

from dashboard.bond_pdf_service import (
    AGENT_NAME,
    build_palmetto_field_values,
    fill_osi_bond,
    fill_palmetto_bond,
)
from dashboard.palmetto_field_placement import required_text_fields
from dashboard.palmetto_packet_fill import build_palmetto_context, fill_palmetto_packet_forms

FAKE = {
    "name": "SAMPLE, NOT A PERSON",
    "first_name": "NOT",
    "last_name": "SAMPLE",
    "defendant_alias": "FAKE ALIAS",
    "booking_number": "SAMPLE-BOOK-000",
    "case_number": "00-SAMPLE-000",
    "county": "Sample",
    "court_type": "Sample Court",
    "court_date": "01/01/2000",
    "court_time": "9:00 AM",
    "bond_date": "01/02/2000",
    "bond_amount": 2500,
    "charge": "SAMPLE CHARGE ONLY",
    "poa_number": "SAMPLE-POA-0001",
    "poa_numbers": ["SAMPLE-POA-0001", "SAMPLE-POA-0002", "SAMPLE-POA-0003", "SAMPLE-POA-0004"],
    "address": "1 Fake Street",
    "defendant_address": "1 Fake Street",
    "defendant_city": "Sampletown",
    "defendant_state": "FL",
    "defendant_zip": "00000",
    "defendant_phone": "555-010-0000",
    "defendant_email": "sample-defendant@example.invalid",
    "dob": "01/01/1900",
    "defendant_dob": "01/01/1900",
    "defendant_ssn": "000-00-0000",
    "defendant_dl": "S0000000",
    "defendant_dl_state": "FL",
    "defendant_employer": "Fake Employer",
    "indemnitor_name": "SAMPLE INDEMNITOR",
    "indemnitor_address": "2 Fake Street",
    "indemnitor_city": "Sampletown",
    "indemnitor_state": "FL",
    "indemnitor_zip": "00000",
    "indemnitor_phone": "555-010-0001",
    "indemnitor_dob": "02/02/1900",
    "indemnitor_ssn": "000-00-0000",
    "agent_name": "FAKE AGENT RIVERA",
    "agent_license": "X100000",
    "collateral_description": "FAKE COLLATERAL NOTE",
    "relationship": "sample",
    "def_remarks": "FAKE REMARKS",
}


def _annots(pdf: bytes) -> dict:
    """Field name → list of values. Duplicate widget names stay separate.

    pdfplumber drops the title on the two Palmetto widgets that share the
    name AgentField, so those values are read with PyMuPDF. Every other
    field is asserted from the pdfplumber annotation /V.
    """
    found: dict = {}
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        for page in document.pages:
            for annot in page.annots or []:
                title = annot.get("title")
                if not title:
                    continue
                raw = (annot.get("data") or {}).get("V")
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", "replace").strip("\x00")
                else:
                    text = "" if raw is None else str(raw)
                found.setdefault(str(title), []).append(text)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        agents = []
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name == "AgentField":
                    agents.append(widget.field_value or "")
        if agents:
            found["AgentField"] = agents
    finally:
        doc.close()
    return found


def _xref_values(pdf: bytes, name: str) -> list:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        values = []
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name == name:
                    values.append(doc.xref_get_key(widget.xref, "V"))
        return values
    finally:
        doc.close()


def test_palmetto_appearance_keys_and_real_agent():
    pdf = fill_palmetto_bond(FAKE)
    fields = _annots(pdf)
    assert fields["chargestField1"] == ["SAMPLE CHARGE ONLY"]
    assert fields["writtenPremiumAmountField"]
    assert "Two Hundred Fifty" in fields["writtenPremiumAmountField"][0]
    assert fields["calculatedPremiumField"] == ["$250.00"]
    assert fields["AgentField"] == ["FAKE AGENT RIVERA", "FAKE AGENT RIVERA"]
    assert fields["agentBailLicNumField"] == ["X100000"]
    joined = " ".join(value for values in fields.values() for value in values)
    assert "Brendan" not in joined
    assert AGENT_NAME not in joined
    recipe = build_palmetto_field_values(FAKE)[0]
    assert recipe["chargestField1"] == "SAMPLE CHARGE ONLY"
    assert recipe["AgentField"] == "FAKE AGENT RIVERA"
    assert recipe["agentBailLicNumField"] == "X100000"
    assert "chargesField1" not in recipe
    assert "AgentField#0" not in recipe
    assert "writtenPremiumAmount" not in recipe


def test_empty_agent_clears_sample_name():
    data = dict(FAKE)
    data.pop("agent_name")
    data["agent_license"] = "G356764"
    pdf = fill_palmetto_bond(data)
    values = _xref_values(pdf, "AgentField")
    assert len(values) == 2
    for kind, value in values:
        assert "Brendan" not in str(value)
        assert str(value).strip() in ("", "()", "null") or kind == "null"
    assert _annots(pdf)["AgentField"] == ["", ""]


def test_required_palmetto_fields_match_context():
    forms = fill_palmetto_packet_forms(FAKE)
    ctx = build_palmetto_context(FAKE)
    for slug, pdf in forms.items():
        fields = _annots(pdf)
        for spec in required_text_fields(slug):
            expected = ctx.get(spec["data_source"], "")
            assert expected, f"{slug} {spec['name']} source {spec['data_source']} empty"
            got = fields.get(spec["name"]) or []
            assert got, f"{slug} missing widget {spec['name']}"
            assert all(value == expected for value in got), (
                f"{slug} {spec['name']} {got!r} != {expected!r}"
            )
            assert "Brendan" not in expected


def test_form_704_information_sheet_lines():
    """Defendant, two POA lines, and the three signature rules on Form 704."""
    forms = fill_palmetto_packet_forms(FAKE)
    pdf = forms["bail-bond-information-sheet-palmetto"]
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        widgets = {widget.field_name: widget for widget in doc[0].widgets() or []}
    finally:
        doc.close()
    assert "terms_defendant" not in widgets
    assert "terms_poa_numbers" not in widgets
    defendant = widgets["bbis_defendant"]
    poa_1 = widgets["bbis_poa_1"]
    poa_2 = widgets["bbis_poa_2"]
    left = widgets["bbis_sign_left"]
    right = widgets["bbis_sign_right"]
    second = widgets["bbis_sign_right_second"]
    assert defendant.field_value == "SAMPLE, NOT A PERSON"
    assert poa_1.field_value == "SAMPLE-POA-0001"
    assert poa_2.field_value == "SAMPLE-POA-0002"
    assert defendant.rect.x1 < 250
    assert 175 < defendant.rect.y1 < 185
    assert poa_1.rect.x0 > 300
    assert poa_1.rect.y1 < poa_2.rect.y0 + 2
    assert second.rect.y0 > right.rect.y1
    assert left.rect.x1 < right.rect.x0
    assert left.field_type_string == "Signature"
    assert right.field_type_string == "Signature"
    assert second.field_type_string == "Signature"


def test_osi_writer_keys_still_match_widgets():
    pdf = fill_osi_bond({
        "name": "SAMPLE, NOT A PERSON",
        "booking_number": "SAMPLE-BOOK-000",
        "case_number": "00-SAMPLE-000",
        "county": "Sample",
        "bond_amount": 1000,
        "charge": "SAMPLE CHARGE ONLY",
        "court_date": "TBN",
        "poa_number": "SAMPLE-POA",
        "bond_date": "01/02/2000",
    })
    fields = _annots(pdf)
    for key in (
        "DefLastName",
        "DefFirstName",
        "DefCharge1",
        "WrittenPremiumAmount",
        "NumericPremiumAmount",
        "BondAgentName",
        "CaseNum",
    ):
        assert key in fields
    assert fields["DefCharge1"] == ["SAMPLE CHARGE ONLY"]
    assert "WrittenPremiumAmount" in fields
