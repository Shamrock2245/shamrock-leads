"""DocuSeal apply splits multi-document fields and keeps live prefill names.

The fixture is the real template 5 export (203 fields) with signed document
URLs removed. Areas on the header, both FAQ pages, the master waiver, and
both SSA releases must survive unchanged.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

from dashboard.palmetto_docuseal_apply import (
    BLANK_BY_DESIGN,
    ROLE_CHANGES,
    SIGNATURE_GEOMETRY_EXCEPTIONS,
    PalmettoApplyError,
    keeps_live_geometry,
    plan_merge,
)
from dashboard.services.docuseal_service import DocuSealService

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "docuseal" / "template_5.json"
SCRIPT = ROOT / "scripts" / "apply_palmetto_docuseal_fields.py"
SPEC_JSON = ROOT / "templates" / "palmetto" / "palmetto_docuseal_field_spec.json"

# schema attachment uuids for documents the spec does not replace
UNCOVERED = {
    "94f0a909-bc1b-41fb-b247-a0639d9ff244",  # shamrock-paperwork-header
    "d9d27455-4178-4021-8198-da2ebf04dc27",  # faq-cosigners
    "516f06f7-c9f1-4c36-b8b7-678bfb359a17",  # faq-defendants
    "92e39ceb-cfcb-4997-a585-21f095538245",  # master-waiver
    "8fac7f46-bc97-457f-9a3d-446598235378",  # ssa-release (indemnitor copy)
    "841ae2af-9a7e-4454-971e-0e1a429bfe00",  # ssa-release (defendant copy)
}
COVERED = {
    "018e37df-ce04-447c-a942-f509b90e96cb",  # indemnity-agreement-palmetto
    "fdc3373f-e2d1-4dc6-80c4-35e374312ef7",  # defendant-application-palmetto
    "70058184-9b84-453e-930e-27ea4c5a9096",  # surety-terms-palmetto
    "38b6ffec-51c5-4016-90a1-80d62859cab1",  # collateral-receipt-palmetto
}
FILLED_TYPES = {"text", "number", "date"}


def _load():
    template = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return template


def _script():
    spec = importlib.util.spec_from_file_location("apply_palmetto_docuseal_fields", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(plan, slug):
    return next(row for row in plan["documents"] if row["slug"] == slug)


def _areas_on(fields, uuids):
    rows = []
    for field in fields:
        for area in field.get("areas") or []:
            if area.get("attachment_uuid") in uuids:
                rows.append((field.get("uuid"), field.get("name"), field.get("submitter_uuid"), area))
    return rows


def _full_bond():
    """Every prefill key the covered documents ask for. No blank-by-design values."""
    defendant = {
        "name": "SAMPLE NOT A PERSON",
        "first_name": "SAMPLE",
        "middle_name": "Q",
        "last_name": "PERSON",
        "alias": "SAMPLE ALIAS",
        "address": "1 Sample St",
        "city": "Fort Myers",
        "state": "FL",
        "zip": "33901",
        "phone": "555-010-0000",
        "email": "sample@example.invalid",
        "employer": "Fake Employer",
        "dob": "01/01/1990",
        "height": "5-10",
        "weight": "180",
        "eyes": "BRO",
        "hair": "BRO",
        "race": "W",
        "ssn": "000-00-0000",
        "dl": "S000-000-00-000-0",
        "dl_state": "FL",
    }
    indemnitor = {
        "name": "INDEMNITOR SAMPLE",
        "first_name": "INDEMNITOR",
        "middle_name": "M",
        "last_name": "SAMPLE",
        "address": "8 Ind St",
        "city": "Fort Myers",
        "state": "FL",
        "zip": "33901",
        "phone": "555-010-0010",
        "email": "ind@example.invalid",
        "dob": "02/02/1980",
        "ssn": "000-00-0001",
        "employer": "Ind Employer",
        "employer_address": "9 Ind Work",
    }
    charges = [
        {
            "charge": f"SAMPLE CHARGE {i}",
            "case_number": f"00-0000-CF-000{i}",
            "poa_number": f"SAMPLE-POA-000{i}",
            "bond_amount": 2500,
        }
        for i in range(1, 5)
    ]
    return {
        "surety_id": "palmetto",
        "writing_agent_name": "FAKE AGENT RIVERA",
        "writing_agent_license": "X100000",
        "defendant_name": "SAMPLE NOT A PERSON",
        "indemnitor_name": "INDEMNITOR SAMPLE",
        "coindemnitor_name": "COINDEMNITOR SAMPLE",
        "county": "Lee",
        "court_type": "Circuit",
        "case_number": "00-0000-CF-0001",
        "poa_number": [f"SAMPLE-POA-000{i}" for i in range(1, 5)],
        "booking_number": "SAMPLE-BOOK",
        "court_date": "01/15/2026",
        "bond_amount": 10000,
        "relationship": "parent",
        "defendant": defendant,
        "indemnitor": indemnitor,
        "charge_details": charges,
        "defendant_address": "1 Sample St",
        "defendant_city": "Fort Myers",
        "defendant_state": "FL",
        "defendant_zip": "33901",
        "defendant_address_how_long": "2 years",
        "defendant_former_address": "2 Old St",
        "defendant_former_address_how_long": "1 year",
        "defendant_phone": "555-010-0000",
        "defendant_email": "sample@example.invalid",
        "defendant_employer": "Fake Employer",
        "defendant_boss": "Fake Boss",
        "defendant_employer_how_long": "3 years",
        "defendant_employer_address": "3 Work St",
        "defendant_employer_phone": "555-010-0001",
        "defendant_previous_employment": "Old Job",
        "defendant_previous_employment_how_long": "1 year",
        "defendant_dob": "01/01/1990",
        "defendant_height": "5-10",
        "defendant_weight": "180",
        "defendant_eyes": "BRO",
        "defendant_hair": "BRO",
        "defendant_race": "W",
        "defendant_tattoos": "SAMPLE MARK",
        "defendant_ssn": "000-00-0000",
        "defendant_alias": "SAMPLE ALIAS",
        "defendant_dl": "S000-000-00-000-0",
        "defendant_dl_state": "FL",
        "defendant_spouse_employer": "Spouse Co",
        "defendant_spouse_address": "4 Spouse St",
        "defendant_spouse_phone": "555-010-0002",
        "children_names_ages_1": "Kid 10",
        "children_school_1": "Sample School",
        "children_school_2": "Sample School 2",
        "def_remarks": "Sample only",
        "def_parent_name": "Parent Sample",
        "def_parent_address": "5 Parent St",
        "def_parent_phone": "555-010-0003",
        "def_spouse_parent_name": "Spouse Parent",
        "def_spouse_parent_address": "13 SP St",
        "def_spouse_parent_phone": "555-010-0006",
        "def_sibling_1_name": "Sib One",
        "def_sibling_1_address": "14 Sib St",
        "def_sibling_1_phone": "555-010-0030",
        "def_sibling_2_name": "Sib Two",
        "def_sibling_2_address": "15 Sib St",
        "def_sibling_2_phone": "555-010-0031",
        "def_sibling_3_name": "Sib Three",
        "def_sibling_3_address": "16 Sib St",
        "def_sibling_3_phone": "555-010-0032",
        "def_best_friend_name": "Friend Sample",
        "def_best_friend_address": "6 Friend St",
        "def_best_friend_phone": "555-010-0004",
        "def_attorney_name": "Attorney Sample",
        "def_attorney_address": "7 Attorney St",
        "def_attorney_phone": "555-010-0005",
        "def_vehicle_year": "2010",
        "def_vehicle_make": "Ford",
        "def_vehicle_model": "Focus",
        "def_vehicle_color": "Blue",
        "def_vehicle_plate": "SAMPLE",
        "def_vehicle_purchase_location": "Sample Lot",
        "def_vehicle_amount_owed": "0",
        "def_vehicle_lender": "Sample Lender",
        "indemnitor_address": "8 Ind St",
        "indemnitor_city": "Fort Myers",
        "indemnitor_state": "FL",
        "indemnitor_zip": "33901",
        "indemnitor_phone": "555-010-0010",
        "indemnitor_phone2": "555-010-0011",
        "indemnitor_email": "ind@example.invalid",
        "indemnitor_dob": "02/02/1980",
        "indemnitor_ssn": "000-00-0001",
        "indemnitor_employer": "Ind Employer",
        "indemnitor_employer_address": "9 Ind Work",
        "indemnitor_spouse_name": "Ind Spouse",
        "indemnitor_spouse_employer": "Ind Spouse Co",
        "indemnitor_spouse_employer_address": "10 Spouse Work",
        "reference_1_name": "Ref One",
        "reference_1_address": "11 Ref St",
        "reference_1_phone": "555-010-0020",
        "reference_2_name": "Ref Two",
        "reference_2_address": "12 Ref St",
        "reference_2_phone": "555-010-0021",
    }


def test_fixture_is_the_live_template_5_export():
    template = _load()
    assert template["id"] == 5
    assert template["name"] == "shamrock-palmetto-paperwork-complete"
    assert len(template["fields"]) == 203
    assert [row["name"] for row in template["schema"]] == [
        "shamrock-paperwork-header",
        "faq-cosigners",
        "faq-defendants",
        "master-waiver",
        "indemnity-agreement-palmetto",
        "defendant-application-palmetto",
        "surety-terms-palmetto",
        "collateral-receipt-palmetto",
        "ssa-release",
        "ssa-release",
    ]
    blob = FIXTURE.read_text(encoding="utf-8")
    assert "https://sign.shamrockbailbonds.biz/file/" not in blob
    for doc in template["documents"]:
        assert "url" not in doc
        assert "preview_image_url" not in doc


def test_uncovered_areas_survive_exactly():
    template = _load()
    raw = json.dumps(template)
    plan = plan_merge(template)
    assert json.dumps(template) == raw
    assert _areas_on(plan["fields"], UNCOVERED) == _areas_on(template["fields"], UNCOVERED)
    for row in plan["documents"]:
        if row["coverage"] == "keep":
            assert row["added"] == []
            assert row["moved"] == []
            assert row["removed"] == []
            assert row["kept"]
    originals = {field["uuid"]: field for field in template["fields"]}
    payload = {field["uuid"]: field for field in plan["fields"] if field.get("uuid")}
    # Header + waiver date never touches a covered document.
    wholly = "27c1156a-8257-46d4-b230-3c96f2300a83"
    assert payload[wholly] == originals[wholly]
    initials_uuid = next(field["uuid"] for field in template["fields"] if field["name"] == "defendant_initials_1")
    assert payload[initials_uuid] == originals[initials_uuid]


def test_mixed_field_keeps_uncovered_areas_on_the_original_object():
    template = _load()
    plan = plan_merge(template)
    originals = {field["uuid"]: field for field in template["fields"]}
    payload = {field["uuid"]: field for field in plan["fields"] if field.get("uuid")}

    header = "94f0a909-bc1b-41fb-b247-a0639d9ff244"
    name_uuid = "d8ae60c4-18e2-4677-b375-620d974a5421"
    remnant = payload[name_uuid]
    source = originals[name_uuid]
    assert remnant["name"] == "defendant_name"
    assert remnant["submitter_uuid"] == source["submitter_uuid"]
    assert remnant["areas"] == [area for area in source["areas"] if area["attachment_uuid"] == header]

    waiver = "92e39ceb-cfcb-4997-a585-21f095538245"
    sig_uuid = "c6a72bbd-4bf1-40fe-afae-f46cc82079d3"
    sig = payload[sig_uuid]
    sig_src = originals[sig_uuid]
    assert sig == sig_src
    assert any(area["attachment_uuid"] == waiver for area in sig["areas"])
    assert len(sig["areas"]) == 4

    # All-covered text span is dropped. Each covered document gets its own field.
    assert "fdb47b90-ca53-4e6d-9180-fefc49763b7f" not in payload
    covered_agent = [
        field for field in plan["fields"]
        if field.get("name") == "agent_name"
        and any(area.get("attachment_uuid") in COVERED for area in field.get("areas") or [])
    ]
    assert len(covered_agent) >= 2
    assert all(field.get("uuid") != "fdb47b90-ca53-4e6d-9180-fefc49763b7f" for field in covered_agent)

    for field in plan["fields"]:
        if keeps_live_geometry(field):
            continue
        attachments = {area.get("attachment_uuid") for area in field.get("areas") or []}
        assert not (attachments & COVERED and attachments & UNCOVERED)


def test_covered_documents_use_live_prefill_names():
    template = _load()
    plan = plan_merge(template)
    application = _doc(plan, "defendant-application")
    assert application["coverage"] == "replace"
    assert "defendant_nickname" in application["removed"]
    assert "defendant_alias" in application["added"]
    flat = application["added"] + application["moved"] + application["removed"] + application["kept"]
    assert "defendant_name" in flat
    assert "app_defendant_name" not in flat
    indemnity = _doc(plan, "indemnity-agreement")
    assert "agent_signature_4" in indemnity["kept"]
    assert "agent_signature_4" not in indemnity["removed"]
    waiver = next(row for row in plan["documents"] if row["document"] == "master-waiver")
    assert "agent_signature_4" in waiver["kept"]
    written = []
    for field in plan["fields"]:
        if any(area.get("attachment_uuid") in COVERED for area in field.get("areas") or []):
            written.append(field["name"])
            assert "data_source" not in (field.get("preferences") or {})
    assert "app_defendant_name" not in written
    assert "bbis_defendant" not in written
    assert "defendant_name" in written
    assert "numeric_full_bond_amount" in written
    assert "poa_number" in written
    dates = [
        field for field in plan["fields"]
        if field.get("name") == "today_date"
        and any(area.get("attachment_uuid") in COVERED for area in field.get("areas") or [])
    ]
    assert dates
    for field in dates:
        assert field["type"] == "date"
        assert field["preferences"] == {"format": "MM/DD/YYYY"}


def test_covered_fill_fields_match_prefill_or_the_blank_allowlist():
    template = _load()
    plan = plan_merge(template)
    values = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test",
    ).prefill_values_from_bond(_full_bond())
    for name in BLANK_BY_DESIGN:
        assert name not in values
    seen_blank = set()
    seen_filled = set()
    for field in plan["fields"]:
        if field.get("type") not in FILLED_TYPES:
            continue
        if keeps_live_geometry(field):
            continue
        areas = field.get("areas") or []
        if not any(area.get("attachment_uuid") in COVERED for area in areas):
            continue
        assert not any(area.get("attachment_uuid") in UNCOVERED for area in areas)
        name = field.get("name") or ""
        if name in BLANK_BY_DESIGN:
            seen_blank.add(name)
            continue
        assert name in values, name
        seen_filled.add(name)
    assert seen_blank == set(BLANK_BY_DESIGN)
    assert "defendant_name" in seen_filled
    assert "numeric_full_bond_amount" in seen_filled
    assert values["agent_name"] == "FAKE AGENT RIVERA"


def _areas_within(left, right, tolerance=0.005):
    if left.get("page") != right.get("page"):
        return False
    if left.get("attachment_uuid") != right.get("attachment_uuid"):
        return False
    return all(
        abs(float(left[key]) - float(right[key])) <= tolerance
        for key in ("x", "y", "w", "h")
    )


def test_signature_geometry_exceptions_are_justified():
    for name, reason in SIGNATURE_GEOMETRY_EXCEPTIONS.items():
        assert str(name).strip()
        assert str(reason).strip()


def test_signature_initials_and_date_signed_keep_live_areas():
    """Live signature, initials, and date-signed boxes stay put.

    A miss, or a move larger than half a percent of the page, fails unless
    the field name is on SIGNATURE_GEOMETRY_EXCEPTIONS with a reason.
    """
    template = _load()
    plan = plan_merge(template)
    payload = {field["uuid"]: field for field in plan["fields"] if field.get("uuid")}
    checked = 0
    for field in template["fields"]:
        if not keeps_live_geometry(field):
            continue
        covered = [
            area for area in field.get("areas") or []
            if area.get("attachment_uuid") in COVERED
        ]
        if not covered:
            continue
        name = str(field.get("name") or "")
        if name in SIGNATURE_GEOMETRY_EXCEPTIONS:
            continue
        got = payload.get(field["uuid"])
        assert got is not None, f"missing {field.get('type')} {name or field['uuid']}"
        assert got["name"] == field["name"]
        assert got["type"] == field["type"]
        assert got["submitter_uuid"] == field["submitter_uuid"]
        assert len(got.get("areas") or []) == len(field.get("areas") or [])
        for live_area, put_area in zip(field["areas"], got["areas"]):
            assert _areas_within(live_area, put_area), (name or field["uuid"], live_area, put_area)
        checked += 1
    assert checked >= 8


def test_appearance_bond_is_not_written_onto_template_5():
    template = _load()
    plan = plan_merge(template)
    appearance = _doc(plan, "appearance-bond")
    assert appearance["coverage"] == "not_on_template"
    assert "chargestField1" in appearance["added"]
    assert "AgentField" in appearance["added"]
    written = {field.get("name") for field in plan["fields"]}
    assert "chargestField1" not in written
    assert "writtenPremiumAmountField" not in written
    assert "AgentField" not in written


def test_unknown_attachment_still_fails_closed():
    template = _load()
    template["fields"].append({
        "uuid": "unknown-1",
        "submitter_uuid": "79b7f81b-d78f-4962-ab04-e2cdebbe2fef",
        "name": "orphan",
        "type": "text",
        "required": False,
        "preferences": {},
        "areas": [
            {"page": 0, "attachment_uuid": "not-a-real-attachment", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.02},
        ],
    })
    with pytest.raises(PalmettoApplyError, match="unknown attachment"):
        plan_merge(template)


def test_live_dry_run_lists_every_document_and_sends_nothing(monkeypatch, capsys):
    module = _script()

    def _explode(*_args, **_kwargs):
        raise AssertionError("dry-run sent a request")

    monkeypatch.setattr(module.urllib.request, "urlopen", _explode)
    code = module.main(["--live", str(FIXTURE)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["recommended"] == "clone"
    by_coverage = {}
    for row in payload["documents"]:
        for key in ("added", "moved", "removed", "kept"):
            assert isinstance(row[key], list)
        by_coverage.setdefault(row["coverage"], []).append(row["document"])
    assert "shamrock-paperwork-header" in by_coverage["keep"]
    assert by_coverage["keep"].count("ssa-release") == 2
    assert "defendant-application-palmetto" in by_coverage["replace"]
    assert "appearance-bond" in by_coverage["not_on_template"]


def test_clone_puts_the_clone_and_prints_its_id(monkeypatch, capsys):
    module = _script()
    template = _load()
    clone = json.loads(json.dumps(template))
    clone["id"] = 99
    calls = []

    def _fake(method, url, token, body=None):
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/templates/5/clone"):
            assert body["name"] == module.CLONE_NAME
            return {"id": 99, "name": body["name"]}
        if method == "GET" and url.endswith("/templates/99"):
            return clone
        if method == "PUT" and url.endswith("/templates/99"):
            return {"id": 99}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-token")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setattr(module, "_request", _fake)
    code = module.main(["--clone"])
    assert code == 0
    methods = [(method, url) for method, url, _body in calls]
    assert methods == [
        ("POST", "https://sign.example.invalid/api/templates/5/clone"),
        ("GET", "https://sign.example.invalid/api/templates/99"),
        ("PUT", "https://sign.example.invalid/api/templates/99"),
    ]
    put_fields = calls[2][2]["fields"]
    original = next(field for field in template["fields"] if field["name"] == "defendant_initials_1")
    header = next(field for field in put_fields if field.get("uuid") == original["uuid"])
    assert header == original
    assert "chargestField1" not in {field["name"] for field in put_fields}
    assert not any("/templates/5" in url and method == "PUT" for method, url, _body in calls)
    out = capsys.readouterr().out
    assert "clone_template_id=99" in out
    assert "source_template_id=5" in out


def _overlap_ratio(left: dict, right: dict) -> float:
    """Intersection area divided by the smaller box. 0 when they miss."""
    ax0, ay0 = float(left["x"]), float(left["y"])
    ax1, ay1 = ax0 + float(left["w"]), ay0 + float(left["h"])
    bx0, by0 = float(right["x"]), float(right["y"])
    bx1, by1 = bx0 + float(right["w"]), by0 + float(right["h"])
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    smaller = min(float(left["w"]) * float(left["h"]), float(right["w"]) * float(right["h"]))
    if smaller <= 0:
        return 0.0
    return inter / smaller


def test_rebuilt_text_boxes_do_not_stack():
    """Text and prefill-date boxes on one page stay clear of each other and of live signature boxes.

    More than about 10% of the smaller box is a fail. Any overlap with a
    live signature, initials, or date-signed box is a fail. The Receipt Date
    blank is the live defendant date (uuid 3b8faaa0); a prefilled today_date
    must not sit on it.
    """
    template = _load()
    plan = plan_merge(template)
    texts = []
    locked = []
    for field in plan["fields"]:
        kind = str(field.get("type") or "")
        for area in field.get("areas") or []:
            if area.get("attachment_uuid") not in COVERED:
                continue
            row = (field, area)
            if kind in ("text", "date") and not keeps_live_geometry(field):
                texts.append(row)
            elif kind in ("signature", "initials") or keeps_live_geometry(field):
                locked.append(row)
    assert len(texts) >= 100
    stacked = []
    for index, (left_field, left) in enumerate(texts):
        for right_field, right in texts[index + 1:]:
            if left.get("attachment_uuid") != right.get("attachment_uuid"):
                continue
            if left.get("page") != right.get("page"):
                continue
            ratio = _overlap_ratio(left, right)
            if ratio > 0.10:
                stacked.append((ratio, left_field.get("name"), right_field.get("name")))
    assert stacked == []
    on_signature = []
    for text_field, text_area in texts:
        for lock_field, lock_area in locked:
            if text_area.get("attachment_uuid") != lock_area.get("attachment_uuid"):
                continue
            if text_area.get("page") != lock_area.get("page"):
                continue
            ratio = _overlap_ratio(text_area, lock_area)
            if ratio > 0.005:
                on_signature.append((ratio, text_field.get("name"), lock_field.get("name"), lock_field.get("type")))
    assert on_signature == []
    collateral = "38b6ffec-51c5-4016-90a1-80d62859cab1"
    live_date = next(field for field in template["fields"] if str(field.get("uuid") or "").startswith("3b8faaa0"))
    live_area = next(area for area in live_date["areas"] if area.get("attachment_uuid") == collateral)
    for field, area in texts:
        if area.get("attachment_uuid") != collateral or field.get("name") != "today_date":
            continue
        assert _overlap_ratio(area, live_area) <= 0.005


def test_rebuilt_text_and_date_keep_the_live_role():
    """Prefill boxes stay on the live template 5 submitter unless ROLE_CHANGES says why."""
    for name, reason in ROLE_CHANGES.items():
        assert str(name).strip()
        assert str(reason).strip()
    template = _load()
    submitters = {row["uuid"]: row["name"] for row in template["submitters"]}
    live_roles = {}
    for field in template["fields"]:
        if field.get("type") not in ("text", "date", "number"):
            continue
        role = submitters[field["submitter_uuid"]]
        for area in field.get("areas") or []:
            if area.get("attachment_uuid") not in COVERED:
                continue
            live_roles.setdefault((area["attachment_uuid"], field.get("name") or ""), set()).add(role)
    plan = plan_merge(template)
    checked = 0
    changed = []
    for field in plan["fields"]:
        if field.get("type") not in ("text", "date"):
            continue
        if keeps_live_geometry(field):
            continue
        role = submitters[field["submitter_uuid"]]
        seen = set()
        for area in field.get("areas") or []:
            attachment = area.get("attachment_uuid")
            if attachment not in COVERED or attachment in seen:
                continue
            seen.add(attachment)
            name = field.get("name") or ""
            known = live_roles.get((attachment, name))
            if not known:
                expected = "bondsman"
                matches = role == expected
            elif len(known) == 1:
                expected = next(iter(known))
                matches = role == expected
            else:
                expected = known
                matches = role in known
            if name in ROLE_CHANGES:
                assert not matches, f"{name} is listed as a role change but still matches live {expected}"
                changed.append(name)
                continue
            assert matches, f"{name} role {role} does not match live {expected}"
            checked += 1
    assert checked >= 100
    assert changed == []


def test_committed_spec_matches_generated_spec():
    module = _script()
    generated = module.spec_document()
    committed = json.loads(SPEC_JSON.read_text(encoding="utf-8"))
    assert committed == generated


def _assert_put_identity(template, plan):
    doc_uuids = {row["uuid"] for row in template["documents"]}
    seen = []
    for field in plan["fields"]:
        field_uuid = field.get("uuid")
        assert isinstance(field_uuid, str) and field_uuid.strip(), field.get("name")
        seen.append(field_uuid)
        areas = field.get("areas") or []
        assert areas, field.get("name")
        for area in areas:
            assert area.get("attachment_uuid") in doc_uuids, (field.get("name"), area.get("attachment_uuid"))
    assert len(seen) == len(set(seen))
    return set(seen)


def test_put_fields_have_unique_uuids_on_target_documents():
    """DocuSeal keys prefill by field uuid. Every PUT field has one, and every area cites this template."""
    template = _load()
    source_uuids = {field["uuid"] for field in template["fields"]}
    plan = plan_merge(template)
    written = _assert_put_identity(template, plan)
    kept = written & source_uuids
    assert kept
    for field in plan["fields"]:
        if field["uuid"] not in source_uuids:
            continue
        original = next(item for item in template["fields"] if item["uuid"] == field["uuid"])
        assert field["name"] == original["name"]
        assert field["submitter_uuid"] == original["submitter_uuid"]
    assert written - source_uuids


def test_clone_areas_use_the_clone_document_uuids():
    """A clone issues new document uuids. Areas follow those, and existing field uuids stay."""
    source = _load()
    template = copy.deepcopy(source)
    for document in template["documents"]:
        document["uuid"] = str(uuid.uuid4())
    source_uuids = {field["uuid"] for field in source["fields"]}
    source_documents = {document["uuid"] for document in source["documents"]}
    plan = plan_merge(template)
    written = _assert_put_identity(template, plan)
    for field in plan["fields"]:
        for area in field["areas"]:
            assert area["attachment_uuid"] not in source_documents
    assert written & source_uuids
    assert written - source_uuids
    assert "d8ae60c4-18e2-4677-b375-620d974a5421" in written
