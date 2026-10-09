"""Write Bond goldens for the live templates and the two secondary maps.

The shared prefill golden stays in ``test_write_bond_golden_smoke.py``.
Live OSI template 1 and Palmetto template 5 keep every submission value whose
field name is on the checked-in inventory. DocuSeal matches by name, so a
payload key that is not on the template is dropped, and a template text box
with no payload value stays blank.

Two secondary goldens stay beside those:

* OSI appearance-bond seed map (not the live template 1 widget list).
* ``PALMETTO_FIELDS`` rebuild spec (template 6, not live template 5).

Nothing calls DocuSeal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dashboard.services.docuseal_service import BOND_AGENTS
from dashboard.services.write_bond_template_projection import (
    UNPLACED_MISS_KEYS,
    allowed_sources,
    golden_fields,
    project_template_fields,
    spec_matches_placement,
)
from tests.test_write_bond_golden_smoke import (
    CHARGE_1,
    CHARGE_2,
    FROZEN,
    HOUSE_LICENSE,
    HOUSE_NAME,
    REGEN_ENV,
    _FrozenDateTime,
    _assert_no_network,
    _assert_send_flags,
    _assert_test_poa,
    _body,
    _diff,
    _field_map,
    _payload,
    _post,
    _round_trip,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
JOINED = f"{CHARGE_1}, {CHARGE_2}"

# Premium payment-plan dates. Names end in _date, but they are case data.
_PAYMENT_PLAN_DATES = (
    "first_payment_due_date",
    "final_payment_due_date",
    "payment_due_date_1",
    "payment_due_date_2",
    "payment_due_date_3",
    "payment_due_date_4",
)

# Case-data prefixes. ``def_`` is the defendant application family on template 1.
# ``offense_`` and ``charge_`` are the charge lines. Signer widgets are separate.
_SHOULD_PREFILL = (
    ("defendant_", "defendant"),
    ("def_", "defendant"),
    ("indemnitor_", "indemnitor"),
    ("coindemnitor_", "coindemnitor"),
    ("bond_", "bond"),
    ("poa_", "poa"),
    ("court_", "court"),
    ("charges_", "charges"),
    ("offense_", "charges"),
    ("charge_", "charges"),
    ("agent_", "agent"),
    ("case_", "case"),
)

_LIVE = {
    "osi": {"template_id": 1, "fixture": "docuseal_t1_fields.json"},
    "palmetto": {"template_id": 5, "fixture": "docuseal_t5_fields.json"},
}

_SECONDARY = {
    "osi": {
        "filename": "write_bond_osi_appearance.json",
        "kind": "appearance_bond_seed",
        "label": (
            "OSI appearance-bond field map from the seeded AcroForm. "
            "Not the live DocuSeal template 1 widget list."
        ),
    },
    "palmetto": {
        "filename": "write_bond_palmetto_spec.json",
        "kind": "palmetto_rebuild_spec",
        "label": (
            "PALMETTO_FIELDS rebuild spec (template 6). "
            "Not the live DocuSeal template 5 widget list."
        ),
    },
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Same offline env as the shared golden smoke. Template ids stay local."""
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)
    monkeypatch.setenv("BOND_AGENCY_ADDRESS", "1 Sample Street, Sample City, FL 00000")
    monkeypatch.setenv("BOND_AGENT_PHONE", "5550100000")
    monkeypatch.delenv("PAPERWORK_PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.datetime",
        _FrozenDateTime,
    )


def _type_list(raw):
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [str(raw or "")]


def _classify_unfilled(field):
    """Signer widgets versus case-data names that got no payload value."""
    name = str(field.get("name") or "")
    lowered = name.lower()
    types = {item.lower() for item in _type_list(field.get("type"))}
    if types & {"signature", "initials", "checkbox", "radio"}:
        return "signer"
    if any(token in lowered for token in ("signature", "initials", "checkbox", "radio")):
        return "signer"
    case_date = (
        lowered.endswith("_dob")
        or "birth" in lowered
        or lowered.startswith("court_")
        # Premium payment-plan dates. The staff-test scrub still removes them.
        or "payment_due_date" in lowered
    )
    if "payment_due_date" in lowered:
        return "payment"
    signing_date = (
        lowered.startswith("today_")
        or "signed" in lowered
        or lowered in {"date"}
        or lowered.endswith("_date")
        or "_date_" in lowered
    )
    if signing_date and not case_date:
        return "signer"
    if "date" in types and not case_date and not any(
        lowered.startswith(prefix) for prefix, _label in _SHOULD_PREFILL
    ):
        return "signer"
    for prefix, label in _SHOULD_PREFILL:
        if lowered.startswith(prefix):
            return label
    return "other"


def _assert_payment_plan_dates(report, inventory):
    """Due dates on the premium plan stay case data. The scrub still blanks them."""
    names = {row["name"] for row in inventory["fields"]}
    by_class = {row["name"]: row["class"] for row in report["unfilled_should_prefill"]}
    for name in _PAYMENT_PLAN_DATES:
        if name not in names:
            continue
        assert _classify_unfilled({"name": name, "type": "text"}) == "payment"
        assert name not in report["unfilled_signer"]
        assert name not in report["fields"]
        assert by_class[name] == "payment"


def _load_inventory(surety_id):
    spec = _LIVE[surety_id]
    path = FIXTURE_DIR / spec["fixture"]
    document = json.loads(path.read_text(encoding="utf-8"))
    fields = document.get("fields") or []
    names = [row.get("name") for row in fields]
    assert names == sorted(names)
    assert len(names) == len(set(names))
    for row in fields:
        assert set(row) <= {"name", "type", "readonly", "readonly_mixed"}
        assert row["name"]
        assert isinstance(row["readonly"], bool)
    meta = document.get("_meta") or {}
    assert meta.get("template_id") == spec["template_id"]
    assert meta.get("unique_named_fields") == len(fields)
    return document


def _live_report(field_map, inventory):
    by_name = {row["name"]: row for row in inventory["fields"]}
    fields = {
        name: {
            "value": entry["value"],
            "readonly": entry["readonly"],
            "submitter_role": list(entry["submitter_role"]),
        }
        for name, entry in field_map.items()
        if name in by_name
    }
    dropped = sorted(set(field_map) - set(by_name))
    should = []
    signer = []
    other = []
    for name, row in by_name.items():
        if name in field_map:
            continue
        kind = _classify_unfilled(row)
        if kind == "signer":
            signer.append(name)
            continue
        item = {
            "class": kind,
            "name": name,
            "readonly": row["readonly"],
            "type": row["type"],
        }
        if kind == "other":
            other.append(item)
        else:
            should.append(item)
    return {
        "fields": fields,
        "dropped_payload_keys": dropped,
        "unfilled_should_prefill": should,
        "unfilled_signer": signer,
        "unfilled_other": other,
    }


def _secondary_path(surety_id):
    return GOLDEN_DIR / _SECONDARY[surety_id]["filename"]


def _live_path(surety_id):
    template_id = _LIVE[surety_id]["template_id"]
    return GOLDEN_DIR / f"write_bond_{surety_id}_t{template_id}_live.json"


def _full_path(surety_id):
    template_id = _LIVE[surety_id]["template_id"]
    return GOLDEN_DIR / f"write_bond_{surety_id}_t{template_id}_full.json"


def _load_secondary(surety_id):
    payload = json.loads(_secondary_path(surety_id).read_text(encoding="utf-8"))
    fields = {key: value for key, value in payload.items() if not key.startswith("_")}
    return payload.get("_meta") or {}, fields


def _load_live(surety_id):
    return json.loads(_live_path(surety_id).read_text(encoding="utf-8"))


def _write_secondary(surety_id, fields):
    spec = _SECONDARY[surety_id]
    document = {
        "_meta": {
            "surety_id": surety_id,
            "kind": spec["kind"],
            "label": spec["label"],
            "frozen_at": FROZEN.isoformat(),
            "note": (
                "Secondary map, not the live template. Rewrite only with "
                "WRITE_BOND_REGEN_GOLDEN=1 via scripts/regen_write_bond_goldens.py. "
                "CI must not set that flag."
            ),
        },
    }
    document.update(fields)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _secondary_path(surety_id).write_text(text, encoding="utf-8")


def _write_live(surety_id, report):
    spec = _LIVE[surety_id]
    document = {
        "_meta": {
            "surety_id": surety_id,
            "template_id": spec["template_id"],
            "kind": "live_template",
            "inventory": f"tests/fixtures/{spec['fixture']}",
            "filled_count": len(report["fields"]),
            "frozen_at": FROZEN.isoformat(),
            "note": (
                "Submission values whose names exist on the live template. "
                "dropped_payload_keys are payload names DocuSeal would ignore. "
                "unfilled_should_prefill are template fields that match case-data "
                "name patterns and received no value. Class payment is a premium "
                "payment-plan due date, not a signing date. unfilled_signer are "
                "signature, initial, checkbox, radio, and signing-date widgets. "
                "Rewrite only with WRITE_BOND_REGEN_GOLDEN=1. CI must not set that flag."
            ),
        },
        "fields": report["fields"],
        "dropped_payload_keys": report["dropped_payload_keys"],
        "unfilled_should_prefill": report["unfilled_should_prefill"],
        "unfilled_signer": report["unfilled_signer"],
        "unfilled_other": report["unfilled_other"],
    }
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _live_path(surety_id).write_text(text, encoding="utf-8")


def _write_full(surety_id, report):
    spec = _LIVE[surety_id]
    document = {
        "_meta": {
            "surety_id": surety_id,
            "template_id": spec["template_id"],
            "kind": "live_template_full",
            "inventory": f"tests/fixtures/{spec['fixture']}",
            "filled_count": len(report["fields"]),
            "frozen_at": FROZEN.isoformat(),
            "note": (
                "Fully populated synthetic staff test case. Same sections as the "
                "sparse live golden: submission values whose names exist on the "
                "live template, dropped payload keys, and template fields with no "
                "value. Phone and payment or premium submission fields are removed "
                "by the staff-test contact scrub before this payload is captured. "
                "Class payment on an unfilled due date is the premium payment plan, "
                "not a signing date. "
                "Rewrite only with WRITE_BOND_REGEN_GOLDEN=1. CI must not set that flag."
            ),
        },
        "fields": report["fields"],
        "dropped_payload_keys": report["dropped_payload_keys"],
        "unfilled_should_prefill": report["unfilled_should_prefill"],
        "unfilled_signer": report["unfilled_signer"],
        "unfilled_other": report["unfilled_other"],
    }
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _full_path(surety_id).write_text(text, encoding="utf-8")


_FULL_CHARGE_1 = "TEST CHARGE ONE"
_FULL_CHARGE_2 = "TEST CHARGE TWO"


def _person(**extra):
    base = {
        "first_name": "TEST",
        "middle_name": "T",
        "last_name": "PARTY",
        "dob": "1900-01-01",
        "dl": "TEST-DL-0000",
        "dl_state": "XX",
        "ssn": "000-00-0000",
        "phone": "555-0100",
        "address": "TEST ADDRESS 1",
        "city": "TEST CITY",
        "state": "XX",
        "zip": "00000",
        "employer": "TEST EMPLOYER",
        "employer_phone": "555-0101",
        "employer_address": "TEST ADDRESS 2",
        "employer_how_long": "TEST TENURE",
    }
    base.update(extra)
    return base


def _full_body(surety_id):
    tag = surety_id.upper()
    defendant = _person(
        last_name="DEFENDANT",
        phone="555-0100",
        hair="TEST HAIR",
        eyes="TEST EYES",
        race="TEST RACE",
        sex="X",
        height="0-00",
        weight="0",
        tattoos="TEST TATTOO",
        alias="TEST ALIAS",
        address_how_long="TEST DURATION",
        former_address="TEST ADDRESS 3",
        former_address_how_long="TEST DURATION 2",
        boss="TEST BOSS",
        previous_employment="TEST PRIOR EMPLOYER",
        previous_employment_how_long="TEST TENURE 2",
        parent_name="TEST PARENT",
        parent_phone="555-0102",
        parent_address="TEST ADDRESS 4",
        spouse_name="TEST SPOUSE",
        spouse_phone="555-0103",
        spouse_address="TEST ADDRESS 5",
        spouse_employer="TEST SPOUSE EMPLOYER",
        spouse_parent_name="TEST SPOUSE PARENT",
        spouse_parent_phone="555-0104",
        spouse_parent_address="TEST ADDRESS 6",
        best_friend_name="TEST FRIEND",
        best_friend_phone="555-0105",
        best_friend_address="TEST ADDRESS 7",
        attorney_name="TEST ATTORNEY",
        attorney_phone="555-0106",
        attorney_address="TEST ADDRESS 8",
        vehicle_year="1900",
        vehicle_make="TEST MAKE",
        vehicle_model="TEST MODEL",
        vehicle_color="TEST COLOR",
        vehicle_plate="TEST-PLATE",
        vehicle_lender="TEST LENDER",
        vehicle_amount_owed="1.00",
        vehicle_purchase_location="TEST DEALER",
        facebook="TEST FACEBOOK",
        instagram="TEST INSTAGRAM",
        prior_arrests="TEST PRIOR ARRESTS",
        prior_convicted="TEST PRIOR CONVICTED",
        prior_offense="TEST PRIOR OFFENSE",
        remarks="TEST REMARKS",
        sibling_1_name="TEST SIBLING ONE",
        sibling_1_phone="555-0107",
        sibling_1_address="TEST ADDRESS 9",
        sibling_2_name="TEST SIBLING TWO",
        sibling_2_phone="555-0108",
        sibling_2_address="TEST ADDRESS 10",
        sibling_3_name="TEST SIBLING THREE",
        sibling_3_phone="555-0109",
        sibling_3_address="TEST ADDRESS 11",
        children_names_ages_1="TEST CHILD ONE 1900",
        children_names_ages_2="TEST CHILD TWO 1900",
        children_school_1="TEST SCHOOL ONE",
        children_school_2="TEST SCHOOL TWO",
    )
    indemnitor = _person(
        last_name="INDEMNITOR",
        phone="555-0110",
        phone2="555-0111",
        work_phone="555-0112",
        employer_phone="555-0113",
        relationship="TEST RELATION",
        vehicle_year="1900",
        vehicle_make="TEST MAKE",
        vehicle_model="TEST MODEL",
        vehicle_color="TEST COLOR",
        mortgage_co="TEST MORTGAGE",
        mortgage_amount="1.00",
        spouse_name="TEST IND SPOUSE",
        spouse_dl="TEST-DL-0001",
        spouse_ssn="000-00-0000",
        spouse_employer="TEST IND SPOUSE EMPLOYER",
        spouse_employer_address="TEST ADDRESS 12",
        spouse_phone="555-0114",
        spouse_work_phone="555-0115",
        ref1Name="TEST REFERENCE ONE",
        ref1Phone="555-0116",
        ref1Address="TEST ADDRESS 13",
        ref1Relation="TEST RELATION",
        ref2Name="TEST REFERENCE TWO",
        ref2Phone="555-0117",
        ref2Address="TEST ADDRESS 14",
        ref2Relation="TEST RELATION",
    )
    coindemnitor = _person(
        last_name="COINDEMNITOR",
        phone="555-0118",
        relationship="TEST CO RELATION",
    )
    return {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-FULL-{tag}",
        "case_number": f"TEST-CASE-FULL-{tag}",
        "packet_id": f"PKT-TEST-FULL-{tag}",
        "defendant_name": "TEST DEFENDANT",
        "indemnitor_name": "TEST INDEMNITOR",
        "coindemnitor_name": "TEST COINDEMNITOR",
        "bond_amount": 2000,
        "premium_amount": "1.00",
        # Staff-test mode strips write-book overrides, so the case county has
        # to stay on the Florida write book. Party addresses stay TEST / XX.
        "county": "Lee",
        "state": "FL",
        "court_date": "1900-01-01",
        "court_time": "00:00",
        "court_type": "TEST COURT",
        "court_location": "TEST COURTHOUSE",
        "facility": "TEST JAIL",
        "charges": f"{_FULL_CHARGE_1} | {_FULL_CHARGE_2}",
        "charge_details": [
            {
                "charge": _FULL_CHARGE_1,
                "bond_amount": "1000",
                "case_number": f"TEST-CASE-FULL-{tag}",
                "poa_number": "TEST-POA-0001",
            },
            {
                "charge": _FULL_CHARGE_2,
                "bond_amount": "1000",
                "case_number": f"TEST-CASE-FULL-{tag}",
                "poa_number": "TEST-POA-0001",
            },
        ],
        "down_payment_amount": "1.00",
        "balance_financed_amount": "1.00",
        "number_of_payments": "1",
        "payment_amount": "1.00",
        "first_payment_due_date": "1900-01-01",
        "final_payment_due_date": "1900-01-01",
        "payment_due_date_1": "1900-01-01",
        "payment_amount_1": "1.00",
        "payment_due_date_2": "1900-01-02",
        "payment_amount_2": "1.00",
        "payment_due_date_3": "1900-01-03",
        "payment_amount_3": "1.00",
        "payment_due_date_4": "1900-01-04",
        "payment_amount_4": "1.00",
        "collateral_description": "TEST COLLATERAL",
        "defendant": defendant,
        "indemnitor": indemnitor,
        "coindemnitor": coindemnitor,
        "send_email": True,
        "send_sms": True,
    }


def _finalize_full(monkeypatch, surety_id):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(monkeypatch, _full_body(surety_id))
    response = result["response"]
    assert response.status_code == 200, response.text
    _assert_no_network(result)
    payload = _payload(result["captured"])
    assert payload["template_id"] == _LIVE[surety_id]["template_id"]
    _assert_send_flags(payload)
    _assert_test_poa(payload)
    return payload


def _values(payload):
    return {name: entry["value"] for name, entry in _field_map(payload).items()}


def _finalize(monkeypatch, surety_id):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(monkeypatch, _body(surety_id))
    response = result["response"]
    assert response.status_code == 200, response.text
    _assert_no_network(result)
    payload = _payload(result["captured"])
    assert payload["template_id"] == _LIVE[surety_id]["template_id"]
    _assert_send_flags(payload)
    _assert_test_poa(payload)
    return payload


def test_palmetto_spec_matches_placement():
    assert spec_matches_placement()


def test_live_inventories_are_name_type_readonly_only():
    """Fixtures keep names, types, and readonly. No geometry and no sample people."""
    osi = _load_inventory("osi")
    palmetto = _load_inventory("palmetto")
    assert len(osi["fields"]) == 227
    assert len(palmetto["fields"]) == 156
    assert osi["_meta"]["blank_names_omitted"] == 3
    assert palmetto["_meta"]["blank_names_omitted"] == 2
    osi_names = {row["name"] for row in osi["fields"]}
    palmetto_names = {row["name"] for row in palmetto["fields"]}
    assert {"offense_1", "offense_2", "offense_3", "offense_4", "charges_summary"} <= osi_names
    assert "charges_summary" in palmetto_names
    assert "offense_2" not in palmetto_names
    assert "charge_line_2" not in palmetto_names
    assert "charge_line_2" not in osi_names
    blob = json.dumps({"osi": osi, "palmetto": palmetto})
    assert "areas" not in blob
    assert "default_value" not in blob
    assert "@" not in blob


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_write_bond_secondary_maps(monkeypatch, surety_id):
    """Appearance-bond seed and the Palmetto rebuild spec. Not the live widgets."""
    payload = _finalize(monkeypatch, surety_id)
    values = _values(payload)
    assert values["agent_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert BOND_AGENTS[HOUSE_LICENSE]["agent_name"] == HOUSE_NAME
    assert values["offense_1"] == CHARGE_1
    assert values["offense_2"] == CHARGE_2
    assert values["charge_line_2"] == CHARGE_2
    assert values["charges"] == JOINED
    assert values["charges_summary"] == JOINED
    assert values["court_datetime"] == values["court_date"]
    assert values["bond_amount_written"] == values["bond_amount_words"]
    assert values["full_bond_amount_words"] == values["bond_amount_words"]
    assert values["bond_amount_words"] != values["bond_amount"]
    for key in UNPLACED_MISS_KEYS:
        assert key in values

    projected = project_template_fields(surety_id, values)
    for name, entry in projected.items():
        allowed = allowed_sources(surety_id, name)
        assert entry["source"] in allowed, (name, entry["source"], allowed)
        if name == "IndNameandDefName":
            assert entry["value"] == f"{values['indemnitor_name']} / {values['defendant_name']}"
        else:
            assert entry["value"] == values[entry["source"]]
        assert entry["submitter_role"]
    for key in UNPLACED_MISS_KEYS:
        assert key not in projected

    fields = _round_trip(golden_fields(projected))
    if surety_id == "osi":
        assert fields["DefCharge1"]["value"] == CHARGE_1
        assert fields["DefCharge1Line2"]["value"] == CHARGE_2
        assert fields["DefCharge1"]["value"] != JOINED
        assert "charge_line_2" not in fields
        assert fields["BondAmountCharge1"]["value"] == values["bond_amount"]
        assert fields["BondAmountCharge1"]["value"] != values["bond_amount_words"]
        assert fields["BondAgentName"]["value"] == HOUSE_NAME
        assert fields["BondAgentLicenseNum"]["value"] == HOUSE_LICENSE
        assert fields["CourtDate"]["value"] == values["court_date"]
        assert "Broadway" not in fields["AgencyDetails"]["value"]
        assert "—" in fields["DefCharge1"]["value"]
        assert "'" in fields["DefCharge1"]["value"]
        assert "§" in fields["DefCharge1"]["value"]
    else:
        assert fields["charges_summary"]["value"] == JOINED
        assert fields["charge_line_2"]["value"] == CHARGE_2
        assert fields["charge_line_2"]["value"] == values["offense_2"]
        assert fields["charge_line_2"]["readonly"] is True
        assert fields["court_datetime"]["value"] == values["court_date"]
        assert fields["bond_amount_words"]["value"] == values["bond_amount_written"]
        assert fields["numeric_full_bond_amount"]["value"] != values["bond_amount_words"]
        assert fields["agent_name"]["value"] == HOUSE_NAME
        assert fields["agent_license"]["value"] == HOUSE_LICENSE
        assert "—" in fields["charges_summary"]["value"]
        assert "'" in fields["charges_summary"]["value"]
        assert "§" in fields["charges_summary"]["value"]
        assert fields["charges_summary"]["submitter_role"] == ["bondsman"]

    if os.environ.get(REGEN_ENV) == "1":
        _write_secondary(surety_id, fields)

    meta, expected = _load_secondary(surety_id)
    spec = _SECONDARY[surety_id]
    assert meta.get("kind") == spec["kind"]
    assert meta.get("surety_id") == surety_id
    assert spec["label"].split(".")[0] in meta.get("label", "")
    diff = _diff(expected, fields)
    assert not diff, diff


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_write_bond_live_template_fields(monkeypatch, surety_id):
    """Values DocuSeal would keep, plus the dropped keys and the blank case fields."""
    payload = _finalize(monkeypatch, surety_id)
    field_map = _round_trip(_field_map(payload))
    inventory = _load_inventory(surety_id)
    report = _live_report(field_map, inventory)
    names = {row["name"] for row in inventory["fields"]}

    assert set(report["fields"]) == set(field_map) & names
    assert report["dropped_payload_keys"] == sorted(set(field_map) - names)
    assert "charge_line_2" in report["dropped_payload_keys"]
    assert "charges_summary" in report["fields"]
    assert report["fields"]["charges_summary"]["value"] == JOINED
    assert report["fields"]["agent_name"]["value"] == HOUSE_NAME
    for row in report["unfilled_should_prefill"]:
        assert row["name"] not in field_map
        assert row["class"] != "signer"
        assert row["class"] != "other"
    for name in report["unfilled_signer"]:
        assert name not in field_map
    signer_names = set(report["unfilled_signer"])
    should_names = {row["name"] for row in report["unfilled_should_prefill"]}
    other_names = {row["name"] for row in report["unfilled_other"]}
    assert not (signer_names & should_names & other_names)
    assert signer_names.isdisjoint(should_names)
    assert signer_names.isdisjoint(other_names)
    assert should_names.isdisjoint(other_names)
    covered = set(report["fields"]) | signer_names | should_names | other_names
    assert covered == names
    _assert_payment_plan_dates(report, inventory)

    if surety_id == "osi":
        assert report["fields"]["offense_1"]["value"] == CHARGE_1
        assert report["fields"]["offense_2"]["value"] == CHARGE_2
        assert report["fields"]["offense_1"]["readonly"] is True
        assert "offense_2" not in report["dropped_payload_keys"]
    else:
        assert "offense_1" in report["dropped_payload_keys"]
        assert "offense_2" in report["dropped_payload_keys"]
        assert "offense_2" not in report["fields"]
        assert "charge_line_2" not in report["fields"]

    if os.environ.get(REGEN_ENV) == "1":
        _write_live(surety_id, report)

    expected = _load_live(surety_id)
    meta = expected.get("_meta") or {}
    assert meta.get("kind") == "live_template"
    assert meta.get("template_id") == _LIVE[surety_id]["template_id"]
    assert meta.get("filled_count") == len(report["fields"])
    diff = _diff(expected["fields"], report["fields"])
    assert not diff, diff
    assert expected["dropped_payload_keys"] == report["dropped_payload_keys"]
    assert expected["unfilled_should_prefill"] == report["unfilled_should_prefill"]
    assert expected["unfilled_signer"] == report["unfilled_signer"]
    assert expected["unfilled_other"] == report["unfilled_other"]


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
def test_write_bond_full_template_fields(monkeypatch, surety_id):
    """Second case: every fact the prefill can read, still two charges."""
    payload = _finalize_full(monkeypatch, surety_id)
    field_map = _round_trip(_field_map(payload))
    inventory = _load_inventory(surety_id)
    report = _live_report(field_map, inventory)
    names = {row["name"] for row in inventory["fields"]}
    joined = f"{_FULL_CHARGE_1}, {_FULL_CHARGE_2}"

    assert set(report["fields"]) == set(field_map) & names
    assert report["dropped_payload_keys"] == sorted(set(field_map) - names)
    assert report["fields"]["defendant_name"]["value"] == "TEST DEFENDANT"
    assert report["fields"]["indemnitor_name"]["value"] == "TEST INDEMNITOR / TEST COINDEMNITOR"
    assert report["fields"]["charges_summary"]["value"] == joined
    assert report["fields"]["court_type"]["value"] == "TEST COURT"
    assert "1900-01-01" in json.dumps(report["fields"])
    assert "000-00-0000" in json.dumps(report["fields"])
    assert "TEST-DL-0000" in json.dumps(report["fields"])
    assert "defendant_social_media_password" not in field_map
    assert "defendant_social_media_password" not in report["fields"]
    assert "do-not-copy" not in json.dumps(report["fields"])
    for name in report["fields"]:
        assert "phone" not in name.lower()
        lowered = name.lower()
        assert "premium" not in lowered
        assert "payment" not in lowered
    if surety_id == "osi":
        assert report["fields"]["offense_1"]["value"] == _FULL_CHARGE_1
        assert report["fields"]["offense_2"]["value"] == _FULL_CHARGE_2
        assert "offense_3" not in report["fields"]
        assert "offense_4" not in report["fields"]
    else:
        assert "offense_1" in report["dropped_payload_keys"]
        assert "charge_line_2" not in report["fields"]
        assert report["fields"]["def_how_long_at_address_1"]["value"] == "TEST DURATION"
        assert report["fields"]["def_how_long_at_address_2"]["value"] == "TEST DURATION 2"
        assert report["fields"]["defendant_how_long_at_job"]["value"] == "TEST TENURE"
        assert report["fields"]["defendant_how_long_at_job_2"]["value"] == "TEST TENURE 2"
        assert report["fields"]["defendant_bff_name"]["value"] == "TEST FRIEND"
        assert report["fields"]["defendant_bff_address"]["value"] == "TEST ADDRESS 7"
        assert report["fields"]["defendant_car_year"]["value"] == "1900"
        assert report["fields"]["defendant_car_make"]["value"] == "TEST MAKE"
        assert report["fields"]["defendant_car_model"]["value"] == "TEST MODEL"
        assert report["fields"]["defendant_car_color"]["value"] == "TEST COLOR"
        assert report["fields"]["defendant_car_license_tag"]["value"] == "TEST-PLATE"
        assert report["fields"]["defendant_car_loan_vendor"]["value"] == "TEST LENDER"
        assert report["fields"]["defendant_car_purchase_location"]["value"] == "TEST DEALER"
        assert report["fields"]["defendant_auto_loan"]["value"] == "1.00"
        assert report["fields"]["defendant_marks_tattoos"]["value"] == "TEST TATTOO"
        assert report["fields"]["defendant_parents_name"]["value"] == "TEST PARENT"
        assert report["fields"]["defendant_parents_address"]["value"] == "TEST ADDRESS 4"
        assert report["fields"]["defendant_spouse_employment"]["value"] == "TEST SPOUSE EMPLOYER"
        assert report["fields"]["defendant_spouse_parents_name"]["value"] == "TEST SPOUSE PARENT"
        assert report["fields"]["defendant_spouse_parents_address"]["value"] == "TEST ADDRESS 6"
        assert report["fields"]["defendant_prior_convictions"]["value"] == "TEST PRIOR CONVICTED"
        for scrubbed in (
            "defendant_bff_phone",
            "defendant_parents_phone",
            "defendant_spouse_parents_phone",
            "defendant_work_phone_number",
            "indemnitor_alternate_phone",
            "spouse_employment_phone_number",
        ):
            assert scrubbed not in report["fields"]

    covered = (
        set(report["fields"])
        | set(report["unfilled_signer"])
        | {row["name"] for row in report["unfilled_should_prefill"]}
        | {row["name"] for row in report["unfilled_other"]}
    )
    assert covered == names
    _assert_payment_plan_dates(report, inventory)

    if os.environ.get(REGEN_ENV) == "1":
        _write_full(surety_id, report)

    expected = json.loads(_full_path(surety_id).read_text(encoding="utf-8"))
    meta = expected.get("_meta") or {}
    assert meta.get("kind") == "live_template_full"
    assert meta.get("template_id") == _LIVE[surety_id]["template_id"]
    assert meta.get("filled_count") == len(report["fields"])
    diff = _diff(expected["fields"], report["fields"])
    assert not diff, diff
    assert expected["dropped_payload_keys"] == report["dropped_payload_keys"]
    assert expected["unfilled_should_prefill"] == report["unfilled_should_prefill"]
    assert expected["unfilled_signer"] == report["unfilled_signer"]
    assert expected["unfilled_other"] == report["unfilled_other"]
