"""Per-template Write Bond goldens after the OSI and Palmetto name maps.

The shared prefill golden stays in ``test_write_bond_golden_smoke.py``.
This file projects that same payload onto the checked-in inventories:
the OSI appearance field map, and ``PALMETTO_FIELDS`` / the Palmetto spec.
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
JOINED = f"{CHARGE_1}, {CHARGE_2}"


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


def _golden_path(surety_id):
    return GOLDEN_DIR / f"write_bond_{surety_id}_template.json"


def _load_golden(surety_id):
    payload = json.loads(_golden_path(surety_id).read_text(encoding="utf-8"))
    fields = {key: value for key, value in payload.items() if not key.startswith("_")}
    return payload.get("_meta") or {}, fields


def _write_golden(surety_id, template_id, fields):
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "_meta": {
            "surety_id": surety_id,
            "template_id": template_id,
            "frozen_at": FROZEN.isoformat(),
            "note": (
                "Template fields after the local name map. Rewrite only with "
                "WRITE_BOND_REGEN_GOLDEN=1 via scripts/regen_write_bond_goldens.py. "
                "CI must not set that flag."
            ),
        },
    }
    document.update(fields)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _golden_path(surety_id).write_text(text, encoding="utf-8")


def _values(payload):
    return {name: entry["value"] for name, entry in _field_map(payload).items()}


def test_palmetto_spec_matches_placement():
    assert spec_matches_placement()


@pytest.mark.parametrize("surety_id,template_id", [("osi", 1), ("palmetto", 5)])
def test_write_bond_template_fields(monkeypatch, surety_id, template_id):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(monkeypatch, _body(surety_id))
    response = result["response"]
    assert response.status_code == 200, response.text
    _assert_no_network(result)
    payload = _payload(result["captured"])
    assert payload["template_id"] == template_id
    _assert_send_flags(payload)
    _assert_test_poa(payload)

    values = _values(payload)
    assert values["agent_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert BOND_AGENTS[HOUSE_LICENSE]["agent_name"] == HOUSE_NAME
    assert values["offense_1"] == CHARGE_1
    assert values["offense_2"] == CHARGE_2
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
        assert fields["charges_summary"]["value"] == values["charges"]
        assert fields["court_datetime"]["value"] == values["court_date"]
        assert fields["bond_amount_words"]["value"] == values["bond_amount_written"]
        assert fields["bond_amount_words"]["value"] == values["full_bond_amount_words"]
        assert fields["numeric_full_bond_amount"]["value"] != values["bond_amount_words"]
        assert fields["agent_name"]["value"] == HOUSE_NAME
        assert fields["agent_license"]["value"] == HOUSE_LICENSE
        assert "—" in fields["charges_summary"]["value"]
        assert "'" in fields["charges_summary"]["value"]
        assert "§" in fields["charges_summary"]["value"]
        assert fields["charges_summary"]["submitter_role"] == ["bondsman"]

    if os.environ.get(REGEN_ENV) == "1":
        _write_golden(surety_id, template_id, fields)

    meta, expected = _load_golden(surety_id)
    assert meta.get("template_id") == template_id
    assert meta.get("surety_id") == surety_id
    diff = _diff(expected, fields)
    assert not diff, diff
