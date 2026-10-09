"""Write Bond goldens for the live templates and the two secondary maps.

The shared prefill golden stays in ``test_write_bond_golden_smoke.py``.
Live OSI template 1 keeps every submission value whose field name is on the
checked-in inventory. Charge names the template does not have are omitted
from the payload, so they are not listed as keys DocuSeal would drop.
Palmetto template 5 cannot show this fixture's charge join (140 characters,
125-character box), so finalize returns 422 ``charge_capacity_exceeded``
and does not rewrite the Palmetto success goldens.

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
    )
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
                "name patterns and received no value. unfilled_signer are signature, "
                "initial, checkbox, radio, and signing-date widgets. "
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


def _assert_palmetto_capacity(monkeypatch):
    """The golden join does not fit template 5. Finalize fails closed."""
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(monkeypatch, _body("palmetto"))
    response = result["response"]
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "charge_capacity_exceeded"
    assert body["charge_count"] == 2
    assert body["capacity_unit"] == "characters"
    assert body["capacity"] == 125
    assert "2" in body["message"] and "125" in body["message"]
    assert result["captured"] == []
    _assert_no_network(result)
    return result


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
    if surety_id == "palmetto":
        _assert_palmetto_capacity(monkeypatch)
        # The rebuild spec is not the signed packet. It still copies offense_2
        # onto charge_line_2 when that source is present.
        projected = project_template_fields("palmetto", {
            "offense_2": CHARGE_2,
            "charges_summary": "BATTERY",
            "agent_name": HOUSE_NAME,
            "agent_license": HOUSE_LICENSE,
        })
        assert projected["charge_line_2"]["value"] == CHARGE_2
        assert projected["charge_line_2"]["source"] == "offense_2"
        assert projected["charge_line_2"]["readonly"] is True
        assert projected["charges_summary"]["value"] == "BATTERY"
        return

    payload = _finalize(monkeypatch, surety_id)
    values = _values(payload)
    assert values["agent_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert BOND_AGENTS[HOUSE_LICENSE]["agent_name"] == HOUSE_NAME
    assert values["offense_1"] == CHARGE_1
    assert values["offense_2"] == CHARGE_2
    # charge_line_2 and charges are not widgets on template 1. The payload
    # omits them. The appearance map reads offense_2 for the second line.
    assert "charge_line_2" not in values
    assert "charges" not in values
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
    if surety_id == "palmetto":
        _assert_palmetto_capacity(monkeypatch)
        return

    payload = _finalize(monkeypatch, surety_id)
    field_map = _round_trip(_field_map(payload))
    inventory = _load_inventory(surety_id)
    report = _live_report(field_map, inventory)
    names = {row["name"] for row in inventory["fields"]}

    assert set(report["fields"]) == set(field_map) & names
    assert report["dropped_payload_keys"] == sorted(set(field_map) - names)
    # Charge names the live template lacks are omitted, not sent and dropped.
    assert "charge_line_2" not in field_map
    assert "charge_line_2" not in report["dropped_payload_keys"]
    assert "charges" not in field_map
    assert "charge_1" not in field_map
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
