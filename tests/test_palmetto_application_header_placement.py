"""Palmetto application DEFENDANT, POWER NO., and CASE NO. placement.

Coordinates are the template 6 review clone, proven with a TEST render.
This file does not call DocuSeal.
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.palmetto_docuseal_apply import plan_merge
from dashboard.palmetto_field_placement import fields_for

# Same print-size rule as #140. A box under 13.98pt auto-sizes to 7pt.
# 14pt or more gives 11pt. An explicit font_size wins.
_ELEVEN_PT_LINE_PT = 13.98


def _print_pt(box_h: float, font_size=None) -> int:
    if font_size not in (None, ""):
        return int(float(font_size))
    if float(box_h) < _ELEVEN_PT_LINE_PT:
        return 7
    return 11

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "docuseal" / "template_5.json"
SPEC_JSON = ROOT / "templates" / "palmetto" / "palmetto_docuseal_field_spec.json"

# Proven TEST-render widths (pt). Helvetica in PyMuPDF is not the render width.
_POA_AT_11 = 114.7
_DEFENDANT_AT_7 = 102.0
_CASE_AT_7 = 141.3
_WIDEST_POA = "OSI-P251-NNN-NN-NNNN"
_WORST_DEFENDANT = "CHRISTOPHER WASHINGTON III"
_FAKE_CASES = "TEST-000001, TEST-26CF0002, TEST-C00003"
_BOTTOM = {"valign": "bottom"}


def _rows(slug: str) -> dict:
    return {row["name"]: row for row in fields_for(slug)}


def _rect(row: dict) -> tuple:
    return (row["x"], row["y"], row["w"], row["h"])


def _norms(row: dict) -> tuple:
    return (row["x_norm"], row["y_norm"], row["w_norm"], row["h_norm"])


def _spec_by_name() -> dict:
    payload = json.loads(SPEC_JSON.read_text(encoding="utf-8"))
    return {row["name"]: row for row in payload["fields"]}


def _plan():
    return plan_merge(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _put(plan: dict, slug: str, name: str):
    document = next(row for row in plan["documents"] if row["slug"] == slug)
    attachment = document["attachment_uuid"]
    found = []
    for field in plan["fields"]:
        if field.get("name") != name:
            continue
        areas = [
            area for area in field.get("areas") or []
            if area.get("attachment_uuid") == attachment
        ]
        if areas:
            found.append((field, areas))
    assert len(found) == 1, name
    return found[0]


def _area_norms(areas) -> set:
    return {(area["x"], area["y"], area["w"], area["h"]) for area in areas}


def _overlaps(left: tuple, right: tuple, eps: float = 1e-6) -> bool:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    width = min(ax + aw, bx + bw) - max(ax, bx)
    height = min(ay + ah, by + bh) - max(ay, by)
    return width > eps and height > eps


def test_application_defendant_and_power_areas():
    """DEFENDANT and POWER NO. boxes, and the second poa_number box, match T6."""
    rows = _rows("defendant-application")
    defendant = rows["app_defendant_name"]
    power = rows["app_power_number"]
    bond_no = rows["app_bond_no"]
    body = rows["app_def_name"]

    assert _rect(defendant) == (468, 0, 138, 12.87)
    assert "preferences" not in defendant
    assert defendant["required"] is True
    assert defendant["data_source"] == "defendant_name"

    assert _rect(power) == (466, 27.2, 140, 14)
    assert power["preferences"] == _BOTTOM
    assert "font_size" not in power["preferences"]
    assert "align" not in power["preferences"]
    assert power["required"] is True
    assert power["data_source"] == "poa_number"

    assert _rect(bond_no) == (340, 143, 78, 7)
    assert bond_no["preferences"] == power["preferences"]
    assert bond_no["data_source"] == "poa_number"

    assert _rect(body) == (112, 188, 190, 12)
    assert "preferences" not in body
    assert body["data_source"] == "defendant_name"

    spec = _spec_by_name()
    for name in ("app_defendant_name", "app_power_number", "app_bond_no", "app_def_name"):
        recorded = spec[name]
        assert (recorded["x"], recorded["y"], recorded["w"], recorded["h"]) == _norms(rows[name])
        if "preferences" in rows[name]:
            assert recorded["preferences"] == rows[name]["preferences"]
        else:
            assert "preferences" not in recorded

    plan = _plan()
    def_field, def_areas = _put(plan, "defendant-application", "defendant_name")
    assert def_field["preferences"] == {}
    assert _area_norms(def_areas) == {_norms(defendant), _norms(body)}
    poa_field, poa_areas = _put(plan, "defendant-application", "poa_number")
    assert poa_field["preferences"] == _BOTTOM
    assert "font_size" not in poa_field["preferences"]
    assert _area_norms(poa_areas) == {_norms(power), _norms(bond_no)}


def test_application_case_number_area():
    """CASE NO. is the verified application box. Indemnity case number is not."""
    rows = _rows("defendant-application")
    case = rows["app_case_number"]
    assert _rect(case) == (458, 42, 148, 11.9)
    assert case["preferences"] == _BOTTOM
    assert "font_size" not in case["preferences"]
    assert "align" not in case["preferences"]
    assert case["required"] is True
    assert case["data_source"] == "case_number"

    recorded = _spec_by_name()["app_case_number"]
    assert (recorded["x"], recorded["y"], recorded["w"], recorded["h"]) == _norms(case)
    assert recorded["preferences"] == _BOTTOM
    assert recorded["data_source"] == "case_number"

    indemnity = _rows("indemnity-agreement")["ind_case_number"]
    assert _rect(indemnity) == (460, 54, 140, 13)
    assert "preferences" not in indemnity
    assert indemnity["data_source"] == "case_number"
    ind_spec = _spec_by_name()["ind_case_number"]
    assert "preferences" not in ind_spec
    assert (ind_spec["x"], ind_spec["y"], ind_spec["w"], ind_spec["h"]) == _norms(indemnity)

    plan = _plan()
    case_field, case_areas = _put(plan, "defendant-application", "case_number")
    assert case_field["preferences"] == _BOTTOM
    assert "font_size" not in case_field["preferences"]
    assert _area_norms(case_areas) == {_norms(case)}
    ind_field, ind_areas = _put(plan, "indemnity-agreement", "case_number")
    assert ind_field["preferences"] == {}
    assert _area_norms(ind_areas) == {_norms(indemnity)}


def test_application_header_boxes_do_not_overlap():
    """DEFENDANT, both AGENT boxes, POWER NO., and CASE NO. stay clear of every box.

    POWER NO. starts at y 27.2, below the AGENT boxes' bottom at 27.0.
    CASE NO. starts at y 42.0, below POWER NO.'s bottom at 41.2.
    """
    rows = list(fields_for("defendant-application"))
    by_name = {row["name"]: row for row in rows}
    power = by_name["app_power_number"]
    case = by_name["app_case_number"]
    agent_name = by_name["app_agent_name"]
    agent_license = by_name["app_agent_license"]

    assert _rect(power) == (466, 27.2, 140, 14)
    assert _rect(case) == (458, 42, 148, 11.9)
    assert agent_name["y"] + agent_name["h"] == 27
    assert agent_license["y"] + agent_license["h"] == 27
    assert power["y"] == 27.2
    assert power["y"] > agent_name["y"] + agent_name["h"]
    assert power["y"] > agent_license["y"] + agent_license["h"]
    assert round(power["y"] + power["h"], 2) == 41.2
    assert case["y"] == 42
    assert case["y"] > power["y"] + power["h"]

    watched = (
        "app_defendant_name",
        "app_agent_name",
        "app_agent_license",
        "app_power_number",
        "app_case_number",
    )
    overlaps = []
    for name in watched:
        target = _rect(by_name[name])
        for other in rows:
            if other is by_name[name]:
                continue
            if _overlaps(target, _rect(other)):
                overlaps.append((name, other["name"]))
    assert overlaps == []


def test_defendant_and_power_text_fits_at_the_print_size():
    """11pt when the box is 14pt or taller; 7pt under 13.98pt. font_size wins when set.

    The widest POA format is about 114.7pt at 11pt and fits the 140pt POWER NO.
    box. A 26-character defendant name is about 102pt at 7pt and fits the 138pt
    DEFENDANT box. Neither box locks font_size.
    """
    rows = _rows("defendant-application")
    defendant = rows["app_defendant_name"]
    power = rows["app_power_number"]
    assert _print_pt(13.97) == 7
    assert _print_pt(13.98) == 11
    assert _print_pt(13, 11) == 11
    assert "preferences" not in defendant
    assert "font_size" not in (power.get("preferences") or {})
    assert _rect(defendant) == (468, 0, 138, 12.87)
    assert _rect(power) == (466, 27.2, 140, 14)
    assert _print_pt(defendant["h"]) == 7
    assert _print_pt(power["h"]) == 11

    assert len(_WIDEST_POA) == 20
    assert _POA_AT_11 <= power["w"] == 140
    assert abs(_POA_AT_11 - 114.7) < 0.05
    assert len(_WORST_DEFENDANT) == 26
    assert abs(160 * 7 / 11 - _DEFENDANT_AT_7) < 1
    assert _DEFENDANT_AT_7 <= defendant["w"] == 138


def test_case_number_text_fits_at_seven_pt():
    """CASE NO. auto-sizes to 7pt. A two-case value about 141.3pt fits 148pt.

    Height under 13.98pt selects 7pt. An 11pt-tall box on the same bottom
    would start inside POWER NO., so font_size is not locked.
    """
    import fitz

    rows = _rows("defendant-application")
    case = rows["app_case_number"]
    power = rows["app_power_number"]
    assert _rect(case) == (458, 42, 148, 11.9)
    assert case["h"] < 13.98
    assert "font_size" not in case["preferences"]
    assert _print_pt(case["h"]) == 7
    assert _print_pt(case["h"], 11) == 11

    bottom = case["y"] + case["h"]
    eleven_pt_top = bottom - 13.98
    assert eleven_pt_top < power["y"] + power["h"]

    drawn = fitz.Font("helv").text_length(_FAKE_CASES, fontsize=7)
    assert drawn >= _CASE_AT_7
    assert abs(_CASE_AT_7 - 141.3) < 0.05
    assert _CASE_AT_7 <= case["w"] == 148


def test_header_values_still_come_from_packet_data():
    """Defendant, POA, and case number still come from the same packet keys."""
    from dashboard.palmetto_packet_fill import build_palmetto_context, values_for_document
    from dashboard.services.docuseal_service import DocuSealService

    packet = {
        "name": "SAMPLE, NOT A PERSON",
        "defendant_name": "SAMPLE, NOT A PERSON",
        "poa_number": "SAMPLE-POA-0001",
        "case_number": "TEST-000001",
        "booking_number": "SAMPLE-BOOK-000",
        "county": "Sample",
        "bond_amount": 2500,
        "charge": "SAMPLE CHARGE ONLY",
        "bond_date": "01/02/2000",
    }
    context = build_palmetto_context(packet)
    assert context["defendant_name"] == packet["defendant_name"]
    assert context["poa_number"] == packet["poa_number"]
    assert context["case_number"] == packet["case_number"]

    values = values_for_document("defendant-application", packet)
    assert values["app_defendant_name"] == context["defendant_name"]
    assert values["app_def_name"] == context["defendant_name"]
    assert values["app_power_number"] == context["poa_number"]
    assert values["app_bond_no"] == context["poa_number"]
    assert values["app_case_number"] == context["case_number"]
    indemnity = values_for_document("indemnity-agreement", packet)
    assert indemnity["ind_case_number"] == context["case_number"]

    prefill = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test",
    ).prefill_values_from_bond(packet)
    assert prefill["defendant_name"] == packet["defendant_name"]
    assert prefill["poa_number"] == packet["poa_number"]
    assert prefill["case_number"] == packet["case_number"]

    rows = _rows("defendant-application")
    assert _rect(rows["app_defendant_name"]) == (468, 0, 138, 12.87)
    assert _rect(rows["app_power_number"]) == (466, 27.2, 140, 14)
    assert _rect(rows["app_case_number"]) == (458, 42, 148, 11.9)
    assert rows["app_defendant_name"]["data_source"] == "defendant_name"
    assert rows["app_power_number"]["data_source"] == "poa_number"
    assert rows["app_bond_no"]["data_source"] == "poa_number"
    assert rows["app_case_number"]["data_source"] == "case_number"
