"""Palmetto collateral-receipt boxes follow the vault payment method.

``collateral_payment_method`` is recorded on the collateral item. It is not
the premium ``down_payment_method`` or ``payment_method``. Staff may store
cash, check, money order, credit card, or other. Alias folding still maps
swipesimple, swipe, and card_present to Credit Card, and financing to Other.
Unknown tokens check nothing.

Template 5 has no Other checkbox. Other on that template sends no Other
value and returns ``collateral_other_box_missing``. Template 6's Other box
is ``cr_other``.
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.collateral_payment_method import (
    DOCUSEAL_NAMES,
    INVALID_METHOD_MESSAGE,
    OTHER_BOX_MISSING_CODE,
    PAYMENT_SOURCES,
    SOURCE_CASH,
    SOURCE_CHECK,
    SOURCE_CREDIT_CARD,
    SOURCE_MONEY_ORDER,
    SOURCE_OTHER,
    checkbox_context,
    payment_source,
    require_staff_method,
)
from dashboard.palmetto_docuseal_apply import resolved_docuseal_name
from dashboard.palmetto_field_placement import PAGE_SIZE, fields_for
from dashboard.palmetto_packet_fill import (
    build_palmetto_context,
    fill_palmetto_document,
)
from dashboard.routers.collateral_api import collateral_bp
from dashboard.services.docuseal_service import (
    DocuSealService,
    build_bond_data_from_dashboard,
)
from dashboard.services.docuseal_signing_ux import (
    field_names_for_template_id,
    submission_fields_from_values,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_JSON = ROOT / "templates" / "palmetto" / "palmetto_docuseal_field_spec.json"
LIVE_NAMES = json.loads(
    (ROOT / "dashboard" / "docuseal_live_template_fields.json").read_text(encoding="utf-8")
)
T5_NAMES = LIVE_NAMES["5"]
T6_NAMES = LIVE_NAMES["6"]

# Points measured on the receipt line. Areas stay these rectangles.
_BOXES = {
    "cr_cash": (41, 422, 11, 11, "collateral_cash"),
    "cr_check": (82, 422, 11, 11, "collateral_check"),
    "cr_money_order": (129, 422, 11, 11, "collateral_money_order"),
    "cr_credit_card": (215, 422, 11, 11, "collateral_credit_card"),
    "cr_other": (446, 422, 11, 11, "collateral_other"),
}

_ONE_BOX = {
    "cash": SOURCE_CASH,
    "check": SOURCE_CHECK,
    "money order": SOURCE_MONEY_ORDER,
    "credit card": SOURCE_CREDIT_CARD,
    "other": SOURCE_OTHER,
}

_PACKET = {
    "bond_case_id": "bond-1",
    "match_id": "match-1",
    "match_status": "validated",
    "defendant_id": "def-1",
    "indemnitor_id": "ind-1",
    "case_number": "26-CF-100",
    "poa_number": "PAL-100",
    "booking_number": "BK123",
    "surety_id": "palmetto",
    "include_bondsman": False,
    "defendant_name": "Sample Defendant",
    "indemnitor_name": "Sample Indemnitor",
    "indemnitor": {"name": "Sample Indemnitor", "email": "ind@example.com"},
    "defendant": {"name": "Sample Defendant", "email": "def@example.com"},
}


class _Mem:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return None

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update((update or {}).get("$set") or {})
        return None


def _checked(data: dict) -> set:
    ctx = checkbox_context(data)
    return {name for name, value in ctx.items() if value == "Yes"}


def _payment_rows(names):
    return [
        name
        for name in names
        if name in DOCUSEAL_NAMES.values() or name in ("cr_other", "collateral_other")
    ]


def test_live_template_inventories_match_the_attached_exports():
    """Names only. Template 5 has the four boxes and no Other. Template 6 has cr_other."""
    assert "collateral_other" not in T5_NAMES
    assert "cr_other" not in T5_NAMES
    for name in (
        "collateral_cash_checkbox",
        "collateral_check_checkbox",
        "collateral_money_order_checkbox",
        "collateral_credit_card_checkbox",
    ):
        assert name in T5_NAMES
        assert name in T6_NAMES
    assert "cr_other" in T6_NAMES
    assert "collateral_other" not in T6_NAMES
    assert field_names_for_template_id(5) == frozenset(T5_NAMES)
    assert field_names_for_template_id("6") == frozenset(T6_NAMES)
    assert "AgencyName" not in T5_NAMES
    assert "CaseNum" not in T5_NAMES


def test_each_stored_method_checks_exactly_one_box():
    for token, source in _ONE_BOX.items():
        data = {"collateral_payment_method": token}
        assert _checked(data) == {source}, token
        assert payment_source(data) == source
        values = DocuSealService.prefill_values_from_bond(data)
        docuseal_name = DOCUSEAL_NAMES[source]
        assert values[docuseal_name] is True
        for other_source, other_name in DOCUSEAL_NAMES.items():
            if other_source == source:
                continue
            assert values.get(other_name, "") == ""


def test_unset_ignores_premium_down_payment_method():
    """A premium method, even when stored, leaves every collateral box blank."""
    samples = [
        {},
        {"down_payment_method": "cash"},
        {"payment_method": "check"},
        {"down_payment_method": "cash", "payment_method": "credit card"},
        {"down_payment_method": "card", "collateral_payment_method": ""},
        {"down_payment_method": "swipesimple", "collateral_payment_method": None},
        {"collateral_payment_method": "   "},
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


def test_aliases_map_card_rails_to_credit_card_and_financing_to_other():
    aliases = {
        "swipesimple": SOURCE_CREDIT_CARD,
        "swipe": SOURCE_CREDIT_CARD,
        "card_present": SOURCE_CREDIT_CARD,
        "card present": SOURCE_CREDIT_CARD,
        "SwipeSimple": SOURCE_CREDIT_CARD,
        " swipe ": SOURCE_CREDIT_CARD,
        "financing": SOURCE_OTHER,
        "Financing": SOURCE_OTHER,
        "cheque": SOURCE_CHECK,
        "card": SOURCE_CREDIT_CARD,
        " credit_card ": SOURCE_CREDIT_CARD,
        "Money-Order": SOURCE_MONEY_ORDER,
    }
    for raw, source in aliases.items():
        assert _checked({"collateral_payment_method": raw}) == {source}, repr(raw)


def test_unknown_values_leave_every_box_blank():
    for raw in ("wire", "bitcoin", "cash/check", True, ["cash"], 0):
        data = {"collateral_payment_method": raw}
        assert payment_source(data) is None
        assert _checked(data) == set()


def test_vault_item_type_is_not_a_payment_method():
    only_type = {"collateral_items": [{"item_type": "Cash Deposit"}]}
    assert payment_source(only_type) is None
    assert _checked(only_type) == set()
    held = {
        "collateral_items": [
            {"item_type": "Cash Deposit", "collateral_payment_method": "check"},
        ],
        "down_payment_method": "cash",
    }
    assert _checked(held) == {SOURCE_CHECK}


def test_disagreeing_collateral_rows_do_not_multi_check():
    mixed = {
        "collateral_items": [
            {"collateral_payment_method": "cash"},
            {"collateral_payment_method": "check"},
        ]
    }
    assert payment_source(mixed) is None
    assert _checked(mixed) == set()
    same = {
        "collateral_payment_method": "money order",
        "collateral_items": [
            {"collateral_payment_method": "Money_Order"},
            {"collateral_payment_method": "money order"},
        ],
    }
    assert _checked(same) == {SOURCE_MONEY_ORDER}


def test_other_description_fills_item_1_on_the_pdf():
    data = {
        "collateral_payment_method": "other",
        "collateral_other_description": "SAMPLE SAFE DEPOSIT KEY",
        "down_payment_reference": "SAMPLE-REF-9",
        "down_payment_method": "cash",
    }
    ctx = build_palmetto_context(data)
    assert ctx["collateral_other"] == "Yes"
    assert ctx["collateral_cash"] == ""
    for name in ("collateral_check", "collateral_money_order", "collateral_credit_card"):
        assert ctx[name] == ""
    assert ctx["collateral_description"] == "SAMPLE SAFE DEPOSIT KEY"
    assert "SAMPLE-REF-9" not in ctx["collateral_description"]
    pdf = fill_palmetto_document("collateral-receipt", data)
    widgets = _pdf_widgets(pdf)
    assert widgets["cr_description"]["value"] == "SAMPLE SAFE DEPOSIT KEY"
    assert widgets["cr_other"]["on"] is True
    for name in ("cr_cash", "cr_check", "cr_money_order", "cr_credit_card"):
        assert widgets[name]["on"] is False


def test_pdf_checks_the_one_matching_box():
    pdf = fill_palmetto_document(
        "collateral-receipt",
        {"collateral_payment_method": " card_present ", "down_payment_method": "cash"},
    )
    widgets = _pdf_widgets(pdf)
    assert widgets["cr_credit_card"]["on"] is True
    for name in ("cr_cash", "cr_check", "cr_money_order", "cr_other"):
        assert widgets[name]["on"] is False
    blank = _pdf_widgets(
        fill_palmetto_document(
            "collateral-receipt",
            {"down_payment_method": "check", "payment_method": "cash"},
        )
    )
    for name in _BOXES:
        assert blank[name]["on"] is False


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
    values = DocuSealService.prefill_values_from_bond(
        {"collateral_payment_method": "cash", "down_payment_method": "check"}
    )
    submitted = submission_fields_from_values(values)
    row = next(item for item in submitted if item["name"] == "collateral_cash_checkbox")
    assert row["default_value"] is True
    assert row["readonly"] is True
    names = {item["name"] for item in submitted}
    assert "collateral_check_checkbox" not in names
    assert "collateral_other" not in names


def test_finalize_body_cannot_override_stored_collateral_method():
    kept = build_bond_data_from_dashboard(
        ctx={
            "collateral_payment_method": "check",
            "down_payment_method": "cash",
        },
        body={
            "collateral_payment_method": "cash",
            "down_payment_method": "credit card",
            "payment_method": "card",
        },
        intake_doc={
            "collateral_payment_method": "money order",
            "payment_method": "cash",
        },
    )
    assert kept["collateral_payment_method"] == "check"
    assert payment_source(kept) == SOURCE_CHECK
    values = DocuSealService.prefill_values_from_bond(kept)
    assert values["collateral_check_checkbox"] is True
    assert values.get("collateral_cash_checkbox", "") == ""
    body_only = build_bond_data_from_dashboard(
        body={"collateral_payment_method": "cash", "down_payment_method": "check"},
        intake_doc={"collateral_payment_method": "other", "down_payment_method": "cash"},
    )
    assert body_only["collateral_payment_method"] == ""
    assert payment_source(body_only) is None


def test_server_rejects_unknown_collateral_payment_method(monkeypatch):
    with pytest.raises(Exception) as rejected:
        require_staff_method("wire")
    assert INVALID_METHOD_MESSAGE in str(rejected.value)
    with pytest.raises(Exception):
        require_staff_method("swipesimple")
    with pytest.raises(Exception):
        require_staff_method("bitcoin")
    assert require_staff_method(" Credit_Card ") == "credit card"
    assert require_staff_method("money-order") == "money order"

    cols = {"collateral_items": _Mem(), "audit_events": _Mem()}
    monkeypatch.setattr(
        "dashboard.services.collateral_service.get_collection",
        lambda name: cols[name],
    )
    app = FastAPI()
    app.include_router(collateral_bp)
    client = TestClient(app)

    bad = client.post(
        "/api/collateral/add",
        json={
            "booking_number": "BK-SAMPLE",
            "defendant_name": "Sample Defendant",
            "collateral_payment_method": "wire",
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_collateral_payment_method"
    assert cols["collateral_items"].docs == []
    assert cols["audit_events"].docs == []

    alias = client.post(
        "/api/collateral/add",
        json={
            "booking_number": "BK-SAMPLE",
            "defendant_name": "Sample Defendant",
            "collateral_payment_method": "swipesimple",
        },
    )
    assert alias.status_code == 400

    ok = client.post(
        "/api/collateral/add",
        json={
            "booking_number": "BK-SAMPLE",
            "defendant_name": "Sample Defendant",
            "collateral_payment_method": " Credit Card ",
            "collateral_other_description": "not used for a card",
        },
    )
    assert ok.status_code == 200, ok.text
    item = ok.json()["item"]
    assert item["collateral_payment_method"] == "credit card"
    payment_audits = [
        row for row in cols["audit_events"].docs
        if row.get("event_type") == "collateral_payment_method_set"
    ]
    assert len(payment_audits) == 1
    assert payment_audits[0]["old_collateral_payment_method"] == ""
    assert payment_audits[0]["new_collateral_payment_method"] == "credit card"
    assert payment_audits[0]["collateral_id"] == item["collateral_id"]
    assert "depositor_phone" not in payment_audits[0]

    changed = client.post(
        f"/api/collateral/payment-method/{item['collateral_id']}",
        json={"collateral_payment_method": "check", "actor": "Desk"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["item"]["collateral_payment_method"] == "check"
    payment_audits = [
        row for row in cols["audit_events"].docs
        if row.get("event_type") == "collateral_payment_method_set"
    ]
    assert len(payment_audits) == 2
    assert payment_audits[1]["old_collateral_payment_method"] == "credit card"
    assert payment_audits[1]["new_collateral_payment_method"] == "check"
    assert payment_audits[1]["actor"] == "Desk"

    refused = client.post(
        f"/api/collateral/payment-method/{item['collateral_id']}",
        json={"collateral_payment_method": "bitcoin"},
    )
    assert refused.status_code == 400
    payment_audits = [
        row for row in cols["audit_events"].docs
        if row.get("event_type") == "collateral_payment_method_set"
    ]
    assert len(payment_audits) == 2
    assert cols["collateral_items"].docs[0]["collateral_payment_method"] == "check"


def _bond(method: str) -> dict:
    data = dict(_PACKET)
    data["collateral_payment_method"] = method
    if method == "other":
        data["collateral_other_description"] = "SAMPLE SAFE DEPOSIT KEY"
    data["down_payment_method"] = "cash"
    return data


async def _submission(method: str, template_id, template_field_names=None):
    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return [{"id": 1, "submission_id": 9, "role": "indemnitor", "slug": "a", "email": "ind@example.com"}]

    svc.create_submission = _create
    result = await svc.create_submission_for_packet(
        template_id=template_id,
        packet_id="pkt-collateral",
        bond_data=_bond(method),
        send_email=False,
        template_field_names=template_field_names,
    )
    submitter = captured["submitters"][0]
    field_names = {row["name"] for row in submitter.get("fields") or []}
    value_names = set((submitter.get("values") or {}))
    return result, field_names, value_names, submitter


@pytest.mark.asyncio
async def test_other_on_t5_warns_and_sends_no_other_value():
    result, fields, values, submitter = await _submission("other", 5)
    assert result["warnings"] == [
        {
            "code": OTHER_BOX_MISSING_CODE,
            "message": result["warnings"][0]["message"],
        }
    ]
    assert "Other" in result["warnings"][0]["message"]
    assert "collateral_other" not in fields
    assert "cr_other" not in fields
    assert "collateral_other" not in values
    assert "cr_other" not in values
    for name in (
        "collateral_cash_checkbox",
        "collateral_check_checkbox",
        "collateral_money_order_checkbox",
        "collateral_credit_card_checkbox",
    ):
        assert name not in fields
    # Template 5 inventory filters collateral names only. CaseNum is not on
    # template 5 and still rides along, which is what the #161 golden records.
    assert "CaseNum" in values
    assert submitter["values"]["CaseNum"] == "26-CF-100"


@pytest.mark.asyncio
async def test_other_on_t6_checks_cr_other():
    result, fields, values, submitter = await _submission("other", 6)
    assert result["warnings"] == []
    assert "cr_other" in fields
    assert "cr_other" in values
    assert submitter["values"]["cr_other"] is True
    row = next(item for item in submitter["fields"] if item["name"] == "cr_other")
    assert row["default_value"] is True
    assert row["readonly"] is True
    assert "collateral_other" not in fields
    assert "collateral_other" not in values


@pytest.mark.asyncio
async def test_submission_builder_never_sends_a_name_the_template_lacks():
    """Passed the attached template 5 inventory, unknown names are not sent."""
    result, fields, values, _submitter = await _submission(
        "other",
        5,
        template_field_names=T5_NAMES,
    )
    assert result["warnings"][0]["code"] == OTHER_BOX_MISSING_CODE
    assert "collateral_other" not in fields
    assert "cr_other" not in fields
    assert "collateral_other" not in values
    assert "CaseNum" not in fields
    assert "CaseNum" not in values
    assert "AgencyName" not in fields
    assert "defendant_name" in values
    cash_result, cash_fields, cash_values, _cash = await _submission(
        "cash",
        5,
        template_field_names=T5_NAMES,
    )
    assert cash_result["warnings"] == []
    assert "collateral_cash_checkbox" in cash_fields
    assert "collateral_cash_checkbox" in cash_values
    assert _payment_rows(cash_fields) == ["collateral_cash_checkbox"]


def test_finalize_other_on_t5_is_warned_on_the_response_and_packet(monkeypatch):
    """Write Bond finalize returns the warning and stores it on the packet."""
    from tests.test_write_bond_golden_smoke import _FrozenDateTime, _body, _payload, _post

    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    monkeypatch.setenv("BOND_AGENCY_ADDRESS", "1 Sample Street, Sample City, FL 00000")
    monkeypatch.setenv("BOND_AGENT_PHONE", "5550100000")
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)
    monkeypatch.delenv("PAPERWORK_PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.datetime",
        _FrozenDateTime,
    )

    from dashboard.services.staff_test_case import prepare_staff_test_case

    async def _prepare(request, body):
        plan = await prepare_staff_test_case(request, body)
        plan.context["collateral_payment_method"] = "other"
        plan.context["collateral_other_description"] = "SAMPLE SAFE DEPOSIT KEY"
        plan.context["down_payment_method"] = "cash"
        return plan

    monkeypatch.setattr(
        "dashboard.services.staff_test_case.prepare_staff_test_case",
        _prepare,
    )
    body = _body("palmetto")
    body["collateral_payment_method"] = "cash"
    body["down_payment_method"] = "cash"
    result = _post(monkeypatch, body)
    response = result["response"]
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["warnings"][0]["code"] == OTHER_BOX_MISSING_CODE
    assert "no Other value was sent" in payload["warnings"][0]["message"]
    packet = result["stores"]["paperwork_packets"].inserts[0]
    assert packet["warnings"][0]["code"] == OTHER_BOX_MISSING_CODE
    assert packet["layout_notes"][0]["message"] == payload["warnings"][0]["message"]
    submission = _payload(result["captured"])
    sent = set()
    for submitter in submission["submitters"]:
        for field in submitter.get("fields") or []:
            sent.add(field["name"])
        for name, value in (submitter.get("values") or {}).items():
            if value not in ("", None, False):
                sent.add(name)
    assert "collateral_other" not in sent
    assert "cr_other" not in sent
    assert "collateral_cash_checkbox" not in sent


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
                found[name] = {
                    "value": value,
                    "on": bool(value) and value not in ("Off", "off", "No", "no"),
                }
    finally:
        doc.close()
    return found
