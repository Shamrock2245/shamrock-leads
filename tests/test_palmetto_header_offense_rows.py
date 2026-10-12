"""Palmetto paperwork-header offense rows (option A4).

Coordinates are the template 6 review clone, proven by TEST render submission
#52 (0.000pt render-vs-model). This file does not call DocuSeal. Template 5
is read only to check that the apply plan adds these four rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz

from dashboard.palmetto_docuseal_apply import plan_merge
from dashboard.palmetto_field_placement import PAGE_SIZE, PACKET_INVENTORY, fields_for
from dashboard.palmetto_packet_fill import (
    build_palmetto_context,
    fill_palmetto_document,
    values_for_document,
)
from dashboard.services.write_bond_template_projection import (
    project_template_fields,
    spec_matches_placement,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_JSON = ROOT / "templates" / "palmetto" / "palmetto_docuseal_field_spec.json"
TEMPLATE_5 = ROOT / "tests" / "fixtures" / "docuseal" / "template_5.json"

# Logo ink bottom and the nearest text above the defendant block.
# Submission 52: logo ink bottom 434.64, nearest other text top 547.47.
_LOGO_BOTTOM = 434.0
_DEFENDANT_TEXT = 547.5

_ROWS = ("offense_1", "offense_2", "offense_3", "offense_4")
_AREAS = {
    "offense_1": (90.0, 446.0, 432.0, 20.0),
    "offense_2": (90.0, 468.0, 432.0, 20.0),
    "offense_3": (90.0, 490.0, 432.0, 20.0),
    "offense_4": (90.0, 512.0, 432.0, 20.0),
}
_PREFERENCES = {"font_size": 7}

# Submission 52, 7pt, worst-case glyph mix.
# 104 characters advanced 428.323pt and stayed on one line.
# 209 characters stayed on two lines; 210 do not.
_ONE_LINE_CHARS = 104
_TWO_LINE_CHARS = 209
_ONE_LINE_ADVANCE = 428.323
_WORST_ADVANCE = _ONE_LINE_ADVANCE / _ONE_LINE_CHARS

# Baselines on the y=512 row: 520.949 and 530.483.
_BASELINE_FROM_TOP = 8.949
_BASELINE_STEP = 9.534

_CHARGES = (
    "SAMPLE CHARGE ONE",
    "SAMPLE CHARGE TWO",
    "SAMPLE CHARGE THREE",
    "SAMPLE CHARGE FOUR",
)


def _rows() -> dict:
    found = {row["name"]: row for row in fields_for("paperwork-header")}
    assert list(found) == list(_ROWS)
    return found


def _rect(row: dict) -> tuple:
    return (row["x"], row["y"], row["w"], row["h"])


def _overlaps(left: tuple, right: tuple) -> bool:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    return min(ax + aw, bx + bw) > max(ax, bx) and min(ay + ah, by + bh) > max(ay, by)


def _lines_that_fit(height: float) -> int:
    """How many 7pt baselines sit inside a row of this height."""
    count = 0
    while _BASELINE_FROM_TOP + count * _BASELINE_STEP <= height + 1e-9:
        count += 1
    return count


def test_header_offense_rows_areas_and_preferences():
    """Four bondsman text rows, read-only, 7pt, at the render-proven boxes."""
    assert PAGE_SIZE["paperwork-header"] == (612.0, 792.0)
    rows = _rows()
    spec = {
        row["name"]: row
        for row in json.loads(SPEC_JSON.read_text(encoding="utf-8"))["fields"]
        if row["document"] == "paperwork-header"
    }
    assert list(spec) == list(_ROWS)
    for name in _ROWS:
        row = rows[name]
        assert _rect(row) == _AREAS[name]
        assert row["preferences"] == _PREFERENCES
        assert row["type"] == "text"
        assert row["role"] == "bondsman"
        assert row["data_source"] == name
        assert row["required"] is False
        assert row["page"] == 1
        recorded = spec[name]
        assert (recorded["x"], recorded["y"], recorded["w"], recorded["h"]) == (
            row["x_norm"],
            row["y_norm"],
            row["w_norm"],
            row["h_norm"],
        )
        assert recorded["preferences"] == _PREFERENCES
        assert recorded["data_source"] == name
        assert recorded["role"] == "bondsman"
    assert spec_matches_placement()


def test_header_offense_rows_do_not_overlap():
    """Consecutive rows leave a 2pt gap and miss every other header-page field."""
    rows = list(fields_for("paperwork-header"))
    by_name = {row["name"]: row for row in rows}
    ordered = [by_name[name] for name in _ROWS]
    for left, right in zip(ordered, ordered[1:]):
        gap = right["y"] - (left["y"] + left["h"])
        assert gap == 2
        assert not _overlaps(_rect(left), _rect(right))
    overlaps = []
    for name in _ROWS:
        target = _rect(by_name[name])
        for other in rows:
            if other["name"] == name:
                continue
            if _overlaps(target, _rect(other)):
                overlaps.append((name, other["name"]))
    assert overlaps == []


def test_header_offense_rows_sit_between_logo_and_defendant():
    """Every row is below the logo bottom and above the Defendant: text."""
    rows = _rows()
    for name in _ROWS:
        row = rows[name]
        assert row["y"] >= _LOGO_BOTTOM
        assert row["y"] + row["h"] <= _DEFENDANT_TEXT


def test_header_offense_values_fill_by_name():
    """Packet offense_1..4 values land on the fields of the same name."""
    packet = {
        "defendant_name": "SAMPLE NOT A PERSON",
        "surety_id": "palmetto",
        "bond_amount": 2500,
    }
    packet.update({name: text for name, text in zip(_ROWS, _CHARGES)})
    context = build_palmetto_context(packet)
    projected = project_template_fields("palmetto", context)
    for index, name in enumerate(_ROWS):
        assert context[name] == _CHARGES[index]
        assert projected[name]["value"] == context[name]
        assert projected[name]["source"] == name
        assert projected[name]["readonly"] is True
        assert projected[name]["submitter_role"] == ["bondsman"]

    filled = values_for_document("paperwork-header", packet)
    assert [filled[name] for name in _ROWS] == list(_CHARGES)

    pdf = fill_palmetto_document("paperwork-header", packet)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        widgets = {widget.field_name: widget.field_value or "" for widget in doc[0].widgets() or []}
    finally:
        doc.close()
    assert [widgets[name] for name in _ROWS] == list(_CHARGES)


def test_header_offense_rows_fit_seven_pt():
    """7pt worst-case mix: 104 characters on one line, 209 on two.

    A 20pt row holds two baselines from submission 52 and not a third.
    """
    rows = _rows()
    for name in _ROWS:
        row = rows[name]
        assert row["preferences"]["font_size"] == 7
        assert row["h"] == 20
        assert row["w"] == 432
        width = row["w"]
        assert _WORST_ADVANCE * _ONE_LINE_CHARS == _ONE_LINE_ADVANCE
        assert _WORST_ADVANCE * _ONE_LINE_CHARS <= width
        assert _WORST_ADVANCE * (_ONE_LINE_CHARS + 1) > width
        assert _WORST_ADVANCE * _TWO_LINE_CHARS <= width * 2
        assert _WORST_ADVANCE * (_TWO_LINE_CHARS + 1) > width * 2
        assert _lines_that_fit(row["h"]) == 2
        assert _BASELINE_FROM_TOP + (2 * _BASELINE_STEP) > row["h"]


def test_paperwork_header_inventory_has_fields():
    """The apply inventory records that paperwork-header now has fields."""
    spec = json.loads(SPEC_JSON.read_text(encoding="utf-8"))
    inventory = {row["slug"]: row for row in spec["inventory"]}
    header = inventory["paperwork-header"]
    assert header == next(row for row in PACKET_INVENTORY if row["slug"] == "paperwork-header")
    assert "not in repo" not in header["docuseal"]
    names = [row["name"] for row in spec["fields"] if row["document"] == "paperwork-header"]
    assert names == list(_ROWS)


def test_header_doc_gains_four_fields():
    """Template 5's header keeps its live fields and gains the four offense rows."""
    plan = plan_merge(json.loads(TEMPLATE_5.read_text(encoding="utf-8")))
    header = next(row for row in plan["documents"] if row["slug"] == "paperwork-header")
    assert header["coverage"] == "keep"
    assert header["added"] == list(_ROWS)
    assert header["removed"] == []
    assert header["moved"] == []
    attachment = header["attachment_uuid"]
    touching = [
        field for field in plan["fields"]
        if any(
            area.get("attachment_uuid") == attachment
            for area in field.get("areas") or []
        )
    ]
    assert len(touching) == len(header["kept"]) + 4
    by_name = {field["name"]: field for field in touching if field["name"] in _ROWS}
    assert list(by_name) == list(_ROWS)
    for name in _ROWS:
        field = by_name[name]
        assert field["preferences"] == _PREFERENCES
        assert field["type"] == "text"
        assert len(field["areas"]) == 1
        area = field["areas"][0]
        assert area["attachment_uuid"] == attachment
        assert area["page"] == 0
