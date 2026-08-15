from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "recon" / "county_recon_inventory.json"
EVIDENCE = ROOT / "docs" / "recon" / "county_source_contract_evidence.json"


def test_versioned_evidence_covers_every_census_county_equivalent_once():
    inventory = json.loads(INVENTORY.read_text())["records"]
    evidence = json.loads(EVIDENCE.read_text())["records"]
    expected = {
        (row["state"], row["county_fips"])
        for row in inventory
        if row["scope_type"] == "county_equivalent"
    }
    actual = {(row["state"], row["county_fips"]) for row in evidence}

    assert len(expected) == 942
    assert len(evidence) == 942
    assert actual == expected


def test_versioned_evidence_is_limited_to_source_contract_fields():
    evidence = json.loads(EVIDENCE.read_text())["records"]
    permitted = {
        "state",
        "county_fips",
        "passive_recommendation",
        "official_source_url",
        "access_posture",
        "evidence_note",
    }
    allowed_recommendations = {"productive", "recon_only", "not_verified", "fail_closed"}

    for row in evidence:
        assert set(row) == permitted
        assert row["passive_recommendation"] in allowed_recommendations
        assert len(row["county_fips"]) == 3 and row["county_fips"].isdigit()
