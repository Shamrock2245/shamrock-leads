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
from dashboard.palmetto_packet_fill import (
    build_palmetto_context,
    fill_palmetto_document,
    fill_palmetto_packet_forms,
)
from dashboard.services.docuseal_service import BOND_AGENTS, house_default_agent

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
    """A registered license fills its holder and does not leave the sample name."""
    data = dict(FAKE)
    data.pop("agent_name")
    data["agent_license"] = "G356764"
    pdf = fill_palmetto_bond(data)
    fields = _annots(pdf)
    assert fields["AgentField"] == ["Kayla Lukesic", "Kayla Lukesic"]
    assert fields["agentBailLicNumField"] == ["G356764"]
    joined = " ".join(str(value) for values in fields.values() for value in values)
    assert "Brendan" not in joined


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


_SAMPLE_AGENT = "Brendan ONeal"
_AGENT_WIDGETS = (
    ("defendant-application", "app_agent_name", "app_agent_license"),
    ("indemnity-agreement", "ind_agent_name", "ind_agent_license"),
)


def _agent_line(data: dict) -> dict:
    """Name and license widgets on the application, indemnity, and appearance bond."""
    found = {}
    for slug, name_key, license_key in _AGENT_WIDGETS:
        fields = _annots(fill_palmetto_document(slug, data))
        found[name_key] = fields[name_key]
        found[license_key] = fields[license_key]
    appearance = _annots(fill_palmetto_document("appearance-bond", data))
    found["AgentField"] = appearance["AgentField"]
    found["agentBailLicNumField"] = appearance["agentBailLicNumField"]
    return found


def _assert_pair(data: dict, name: str, license_no: str) -> None:
    widgets = _agent_line(data)
    assert widgets["app_agent_name"] == [name]
    assert widgets["ind_agent_name"] == [name]
    assert widgets["app_agent_license"] == [license_no]
    assert widgets["ind_agent_license"] == [license_no]
    assert widgets["AgentField"] == [name, name]
    assert widgets["agentBailLicNumField"] == [license_no]
    joined = " ".join(value for values in widgets.values() for value in values)
    assert _SAMPLE_AGENT not in joined
    for other_license, entry in BOND_AGENTS.items():
        if other_license == license_no:
            continue
        assert entry["agent_name"] not in joined
        assert other_license not in joined
    own = BOND_AGENTS.get(license_no)
    if own:
        assert own["agent_name"] == name


def test_agent_license_matches_the_writing_agent_pair(monkeypatch):
    """The license widget is the same BOND_AGENTS pair as the name.

    House default, a license-only sub-agent, and a crossed name/license
    record each print one pair. The template sample spelling is not used.
    An unregistered license with no name still falls through to the house
    pair, and house_default_agent is unchanged.
    """
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)
    assert house_default_agent() == ("Brendan O'Neal", "P139768")
    assert house_default_agent(tenant="shamrock") == ("Brendan O'Neal", "P139768")

    base = {
        "name": "SAMPLE, NOT A PERSON",
        "bond_amount": 1000,
        "county": "Sample",
        "charge": "SAMPLE CHARGE ONLY",
    }
    _assert_pair(base, "Brendan O'Neal", "P139768")
    _assert_pair({**base, "agent_license": "G356764"}, "Kayla Lukesic", "G356764")
    _assert_pair({**base, "agent_license": "W214323"}, "Jason Taylor", "W214323")
    _assert_pair(
        {**base, "agent_name": "Kayla Lukesic", "agent_license": "W214323"},
        "Kayla Lukesic",
        "G356764",
    )
    _assert_pair(
        {**base, "agent_name": "Jason Taylor", "agent_license": "P139768"},
        "Jason Taylor",
        "W214323",
    )
    _assert_pair({**base, "agent_name": _SAMPLE_AGENT}, "Brendan O'Neal", "P139768")
    _assert_pair({**base, "agent_license": "X100000"}, "Brendan O'Neal", "P139768")
    _assert_pair(
        {**base, "agent_name": "FAKE AGENT RIVERA", "agent_license": "X100000"},
        "FAKE AGENT RIVERA",
        "X100000",
    )
