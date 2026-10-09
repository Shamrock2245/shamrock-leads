"""Palmetto collateral-receipt payment boxes follow one stored method.

``active_bonds.down_payment_method`` is the method. Record Bond also stores
``payment_method`` from the same select. Write Bond folds the token through
``normalize_method``. Record Bond strips and does not lowercase. The
active-bond create path stores the raw string, so case and surrounding
whitespace can still be on the document.

The vault stores ``item_type`` (Cash Deposit, Jewelry, and the rest). That
is not a payment method. The bond model does not store several collateral
payment methods, so two disagreeing rows leave every box blank instead of
checking more than one.

OSI's collateral receipt has unplaced printed boxes and no field spec.
Those stay unfilled.
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz

from dashboard.collateral_payment_method import (
    DOCUSEAL_NAMES,
    PAYMENT_SOURCES,
    checkbox_context,
    payment_source,
)
from dashboard.palmetto_docuseal_apply import resolved_docuseal_name
from dashboard.palmetto_field_placement import PAGE_SIZE, fields_for
from dashboard.palmetto_packet_fill import (
    build_palmetto_context,
    fill_palmetto_document,
)
from dashboard.services.docuseal_service import (
    DocuSealService,
    build_bond_data_from_dashboard,
)
from dashboard.services.docuseal_signing_ux import submission_fields_from_values

ROOT = Path(__file__).resolve().parents[1]
SPEC_JSON = ROOT / "templates" / "palmetto" / "palmetto_docuseal_field_spec.json"

# Points measured on the receipt line. Areas stay these rectangles.
_BOXES = {
    "cr_cash": (41, 422, 11, 11, "collateral_cash"),
    "cr_check": (82, 422, 11, 11, "collateral_check"),
    "cr_money_order": (129, 422, 11, 11, "collateral_money_order"),
    "cr_credit_card": (215, 422, 11, 11, "collateral_credit_card"),
    "cr_other": (446, 422, 11, 11, "collateral_other"),
}

# Stored token → the one data_source. Anything outside this table is blank.
_ONE_BOX = {
    "cash": "collateral_cash",
    "check": "collateral_check",
    "cheque": "collateral_check",
    "card": "collateral_credit_card",
    "credit card": "collateral_credit_card",
    "money order": "collateral_money_order",
    "swipesimple": "collateral_other",
    "swipe": "collateral_other",
    "swipe simple": "collateral_other",
    "card present": "collateral_other",
    "financing": "collateral_other",
    "other": "collateral_other",
}


def _checked(data: dict) -> set:
    ctx = checkbox_context(data)
    return {name for name, value in ctx.items() if value == "Yes"}


def test_each_stored_method_checks_exactly_one_box():
    for token, source in _ONE_BOX.items():
        checked = _checked({"down_payment_method": token})
        assert checked == {source}, token
        assert payment_source({"payment_method": token}) == source
        values = DocuSealService.prefill_values_from_bond({"down_payment_method": token})
        docuseal_name = DOCUSEAL_NAMES[source]
        assert values[docuseal_name] is True
        for other_source, other_name in DOCUSEAL_NAMES.items():
            if other_source == source:
                continue
            assert values.get(other_name, "") == ""


def test_case_and_whitespace_variants_fold():
    """Record Bond does not lowercase. Active-bond create does not strip."""
    variants = {
        "Cash": "collateral_cash",
        " cash ": "collateral_cash",
        "CHECK": "collateral_check",
        "  Check  ": "collateral_check",
        "SwipeSimple": "collateral_other",
        " card_present ": "collateral_other",
        "Money_Order": "collateral_money_order",
        "credit-card": "collateral_credit_card",
    }
    for raw, source in variants.items():
        assert _checked({"down_payment_method": raw}) == {source}, repr(raw)


def test_missing_or_unknown_leaves_every_box_blank():
    samples = [
        {},
        {"down_payment_method": None},
        {"down_payment_method": ""},
        {"down_payment_method": "   "},
        {"payment_method": None},
        {"down_payment_method": "wire"},
        {"down_payment_method": "bitcoin"},
        {"down_payment_method": "cash/check"},
        {"down_payment_method": True},
        {"down_payment_method": ["cash"]},
        {"down_payment_method": 0},
    ]
    for data in samples:
        assert payment_source(data) is None
        assert _checked(data) == set()
        values = DocuSealService.prefill_values_from_bond(data)
        for name in DOCUSEAL_NAMES.values():
            assert values.get(name, "") == ""
        submitted = submission_fields_from_values(values)
        submitted_names = {row["name"] for row in submitted}
        assert submitted_names.isdisjoint(DOCUSEAL_NAMES.values())


def test_disagreeing_methods_leave_every_box_blank():
    data = {"down_payment_method": "cash", "payment_method": "check"}
    assert payment_source(data) is None
    assert _checked(data) == set()
    mixed = {"down_payment_method": "cash", "payment_method": "wire"}
    assert payment_source(mixed) is None
    assert _checked(mixed) == set()


def test_vault_item_type_is_not_a_payment_method():
    """Cash Deposit is collateral held, not the receipt's Cash box."""
    data = {
        "down_payment_method": "check",
        "collateral_items": [{"item_type": "Cash Deposit"}],
    }
    assert _checked(data) == {"collateral_check"}
    only_type = {"collateral_items": [{"item_type": "Cash Deposit"}]}
    assert payment_source(only_type) is None
    assert _checked(only_type) == set()


def test_disagreeing_embedded_rows_do_not_multi_check():
    """The bond stores one method. Two different row methods check nothing.

    The collateral vault does not store a payment method. If a payload
    still carries two rows with different methods, the receipt's single
    row of boxes stays blank.
    """
    data = {
        "collateral_items": [
            {"payment_method": "cash"},
            {"payment_method": "check"},
        ],
    }
    assert payment_source(data) is None
    assert _checked(data) == set()
    same = {
        "collateral_items": [
            {"payment_method": "money order"},
            {"down_payment_method": "Money Order"},
        ],
    }
    assert _checked(same) == {"collateral_money_order"}


def test_other_keeps_item_1_description():
    data = {
        "down_payment_method": "other",
        "collateral_description": "SAMPLE WIRE TRANSFER",
        "down_payment_reference": "SAMPLE-REF-9",
    }
    ctx = build_palmetto_context(data)
    assert ctx["collateral_other"] == "Yes"
    assert ctx["collateral_cash"] == ""
    assert ctx["collateral_check"] == ""
    assert ctx["collateral_money_order"] == ""
    assert ctx["collateral_credit_card"] == ""
    assert ctx["collateral_description"] == "SAMPLE WIRE TRANSFER"
    assert "SAMPLE-REF-9" not in ctx["collateral_description"]
    values = DocuSealService.prefill_values_from_bond(data)
    assert values["collateral_other"] is True
    assert "collateral_description" not in values
    pdf = fill_palmetto_document("collateral-receipt", data)
    widgets = _pdf_widgets(pdf)
    assert widgets["cr_description"]["value"] == "SAMPLE WIRE TRANSFER"
    assert widgets["cr_other"]["on"] is True
    for name in ("cr_cash", "cr_check", "cr_money_order", "cr_credit_card"):
        assert widgets[name]["on"] is False


def test_placement_and_spec_keep_box_areas_and_bondsman_role():
    page_w, page_h = PAGE_SIZE["collateral-receipt"]
    placed = {row["name"]: row for row in fields_for("collateral-receipt")}
    spec = json.loads(SPEC_JSON.read_text(encoding="utf-8"))
    spec_rows = {
        row["name"]: row
        for row in spec["fields"]
        if row["name"] in _BOXES
    }
    assert set(spec_rows) == set(_BOXES)
    for name, (x, y, w, h, source) in _BOXES.items():
        row = placed[name]
        assert row["type"] == "checkbox"
        assert row["role"] == "bondsman"
        assert row["required"] is False
        assert row["data_source"] == source
        assert (row["x"], row["y"], row["w"], row["h"]) == (x, y, w, h)
        committed = spec_rows[name]
        assert committed["data_source"] == source
        assert committed["role"] == "bondsman"
        assert committed["type"] == "checkbox"
        assert committed["required"] is False
        assert committed["x"] == round(x / page_w, 6)
        assert committed["y"] == round(y / page_h, 6)
        assert committed["w"] == round(w / page_w, 6)
        assert committed["h"] == round(h / page_h, 6)
        assert resolved_docuseal_name(row) == DOCUSEAL_NAMES[source]
    assert set(PAYMENT_SOURCES) == {source for *_, source in _BOXES.values()}


def test_prefill_of_a_checked_box_is_readonly():
    values = DocuSealService.prefill_values_from_bond({"down_payment_method": "cash"})
    submitted = submission_fields_from_values(values)
    row = next(item for item in submitted if item["name"] == "collateral_cash_checkbox")
    assert row["default_value"] is True
    assert row["readonly"] is True
    names = {item["name"] for item in submitted}
    assert "collateral_check_checkbox" not in names
    assert "collateral_other" not in names


def test_finalize_body_cannot_override_the_bond_method():
    kept = build_bond_data_from_dashboard(
        ctx={"down_payment_method": "check"},
        body={"down_payment_method": "cash"},
    )
    assert kept["down_payment_method"] == "check"
    assert payment_source(kept) == "collateral_check"
    body_only = build_bond_data_from_dashboard(
        body={"down_payment_method": "cash", "payment_method": "card"},
    )
    assert body_only["down_payment_method"] == ""
    assert body_only["payment_method"] == ""
    assert payment_source(body_only) is None
    conflict = build_bond_data_from_dashboard(
        ctx={"down_payment_method": "check"},
        intake_doc={"payment_method": "cash"},
    )
    assert conflict["down_payment_method"] == "check"
    assert conflict["payment_method"] == "cash"
    assert payment_source(conflict) is None


def test_pdf_checks_the_one_matching_box():
    pdf = fill_palmetto_document(
        "collateral-receipt",
        {"down_payment_method": " card "},
    )
    widgets = _pdf_widgets(pdf)
    assert widgets["cr_credit_card"]["on"] is True
    for name in ("cr_cash", "cr_check", "cr_money_order", "cr_other"):
        assert widgets[name]["on"] is False
    blank = _pdf_widgets(fill_palmetto_document("collateral-receipt", {}))
    for name in _BOXES:
        assert blank[name]["on"] is False


def _pdf_widgets(pdf: bytes) -> dict:
    doc = fitz.open(stream=pdf, filetype="pdf")
    found = {}
    try:
        for page in doc:
            for widget in page.widgets() or []:
                name = widget.field_name or ""
                if name not in _BOXES and name != "cr_description":
                    continue
                value = widget.field_value or ""
                on_state = ""
                try:
                    on_state = widget.on_state() or ""
                except Exception:
                    on_state = ""
                found[name] = {
                    "value": value,
                    "on": bool(value) and value not in ("Off", "off", "No", "no"),
                    "on_state": on_state,
                }
    finally:
        doc.close()
    return found
