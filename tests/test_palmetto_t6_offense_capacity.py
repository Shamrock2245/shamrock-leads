"""Template 6 keeps paperwork-header offense rows. Template 5 does not.

The DocuSeal client is stubbed and Mongo is an in-memory fake. No live
call, no send, and no real person. Charges are synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.services.charge_verbatim import template_record
from tests.test_verbatim_bond_charges import _post_finalize, _submission_fields

_CHARGES = (
    "SAMPLE CHARGE ONE, WITH A COMMA",
    "SAMPLE CHARGE TWO — em dash",
    "SAMPLE CHARGE THREE",
    "SAMPLE CHARGE FOUR",
)
_ROW_CAP = 209


def _body(charges):
    return {
        "test_case": True,
        "surety_id": "palmetto",
        "booking_number": "TEST-T6-OFFENSE",
        "case_number": "TEST-CASE-1",
        "packet_id": "PKT-TEST-T6-OFFENSE",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "charges": "PLAIN STRING MUST NOT WIN",
        "charge_details": [
            {
                "charge": text,
                "statute": "Fla. Stat. § 812.014",
                "degree": "2nd degree",
                "case_number": f"TEST-CASE-{index}",
                "bond_amount": 1000,
            }
            for index, text in enumerate(charges, start=1)
        ],
    }


def _offense_values(captured):
    payload, fields = _submission_fields(captured)
    values = payload["submitters"][0]["values"]
    return payload, fields, values


def test_template_6_inventory_has_header_rows_and_live_templates_do_not():
    """T6 lists the four header rows. T5 and T1 inventories stay as they were."""
    osi = template_record("osi")
    palmetto = template_record("palmetto")
    review = json.loads(
        (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "docuseal_charge_capacity.json")
        .read_text(encoding="utf-8")
    )["templates"]["palmetto_t6"]
    assert osi["template_id"] == 1
    assert palmetto["template_id"] == 5
    assert palmetto["offense_rows"] == []
    assert "offense_1" not in palmetto["field_names"]
    assert palmetto["charges_summary_capacity"]["cap"] == 200
    assert review["template_id"] == 6
    assert review["offense_rows"] == [f"offense_{n}" for n in range(1, 5)]
    assert review["offense_row_character_capacity"]["cap"] == _ROW_CAP
    assert "charges_summary_capacity" not in review
    boxes = [box for box in review["charge_boxes"] if str(box["field"]).startswith("offense_")]
    assert [box["field"] for box in boxes] == list(review["offense_rows"])
    for box in boxes:
        assert box["document"] == "paperwork-header.pdf"
        assert box["width_pt"] == 432.0
        assert box["height_pt"] == 20.0
        assert box["page_width"] == 612.0
        assert box["page_height"] == 792.0


def test_template_6_submission_keeps_four_offense_values(monkeypatch):
    """Four offense strings reach create_submission character for character."""
    response, captured, _stores = _post_finalize(
        monkeypatch,
        _body(_CHARGES),
        palmetto_template_id="6",
    )
    assert response.status_code == 200, response.text
    payload, fields, values = _offense_values(captured)
    assert payload["template_id"] == 6
    assert payload["send_email"] is False
    for index, text in enumerate(_CHARGES, start=1):
        name = f"offense_{index}"
        assert values[name] == text
        assert fields[name] == text
        assert values[name].encode("utf-8") == text.encode("utf-8")
    assert "," in values["offense_1"]
    assert "—" in values["offense_2"]
    assert "offense_5" not in values
    assert "PLAIN STRING" not in values["charges_summary"]


def test_template_5_submission_sends_no_offense_keys(monkeypatch):
    """The same four charges aimed at template 5 omit every offense_* name."""
    response, captured, _stores = _post_finalize(
        monkeypatch,
        _body(_CHARGES),
        palmetto_template_id="5",
    )
    assert response.status_code == 200, response.text
    payload, fields, values = _offense_values(captured)
    assert payload["template_id"] == 5
    for key in list(values) + list(fields):
        assert not str(key).startswith("offense_")
    assert values["charges_summary"] == ", ".join(_CHARGES)
    assert fields["charges_summary"] == ", ".join(_CHARGES)


def test_offense_row_of_209_characters_passes(monkeypatch):
    text = "X" * _ROW_CAP
    response, captured, _stores = _post_finalize(
        monkeypatch,
        _body((text,)),
        palmetto_template_id="6",
    )
    assert response.status_code == 200, response.text
    _payload, fields, values = _offense_values(captured)
    assert values["offense_1"] == text
    assert fields["offense_1"] == text
    assert len(values["offense_1"]) == _ROW_CAP
    assert "offense_2" not in values


def test_offense_row_of_210_characters_returns_422(monkeypatch):
    text = "Y" * (_ROW_CAP + 1)
    response, captured, stores = _post_finalize(
        monkeypatch,
        _body((text,)),
        palmetto_template_id="6",
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "charge_capacity_exceeded"
    assert body["capacity_unit"] == "characters"
    assert body["capacity"] == _ROW_CAP
    assert body["row"] == "offense_1"
    assert "offense_1" in body["message"]
    assert text not in body["message"]
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []


def test_five_charges_on_four_rows_returns_422(monkeypatch):
    charges = _CHARGES + ("SAMPLE CHARGE FIVE",)
    response, captured, stores = _post_finalize(
        monkeypatch,
        _body(charges),
        palmetto_template_id="6",
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "charge_capacity_exceeded"
    assert body["capacity_unit"] == "rows"
    assert body["capacity"] == 4
    assert body["charge_count"] == 5
    assert captured == []
    assert stores.get("paperwork_packets") is None or stores["paperwork_packets"].inserts == []
