"""Tests for the 5-point Paperwork Perfection Pre-Flight Gate."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.paperwork import paperwork_bp
from dashboard.services.paperwork_preflight import (
    compute_fl_statutory_min_premium,
    evaluate_paperwork_preflight,
)


def _valid_bond_data(**overrides):
    data = {
        "packet_id": "PKT-PRE-001",
        "bond_case_id": "BC-PRE-001",
        "match_id": "MATCH-PRE-001",
        "defendant_id": "DEF-PRE-001",
        "indemnitor_id": "IND-PRE-001",
        "match_status": "validated",
        "surety_id": "osi",
        "poa_number": "OSI3 778899",
        "case_number": "26-CF-004411",
        "booking_number": "BK-26-4411",
        "county": "Lee",
        "facility": "Lee County Jail",
        "court_date": "2026-12-10 09:00 AM",
        "bond_amount": 10000.0,
        "premium_amount": 1000.0,
        "charges": "893.13 6a - Possession of Controlled Substance",
        "defendant": {
            "name": "John Sample",
            "dob": "1991-05-12",
            "dl": "S514420911720",
            "phone": "2395550101",
            "email": "john.sample@example.invalid",
        },
        "indemnitor": {
            "name": "Jane Sample",
            "dob": "1988-09-03",
            "dl": "S514421888230",
            "phone": "2395550102",
            "email": "jane.sample@example.invalid",
        },
        "indemnitors": [
            {
                "name": "Jane Sample",
                "dob": "1988-09-03",
                "dl": "S514421888230",
                "phone": "2395550102",
                "email": "jane.sample@example.invalid",
            }
        ],
    }
    data.update(overrides)
    return data


def test_preflight_passes_spotless_payload():
    bond = _valid_bond_data()
    arrest = {"charges": "893.13 6a - Possession of Controlled Substance"}
    poa = {"max_bond_value": 25000.0}
    report = evaluate_paperwork_preflight(
        bond_data=bond,
        arrest_doc=arrest,
        poa_doc=poa,
        surety_id="osi",
        include_defendant=True,
    )
    assert report["ready"] is True
    assert report["score"] == 100
    assert report["blocking_issues"] == []
    assert all(c["status"] == "pass" for c in report["checks"].values())


def test_preflight_blocks_verbatim_charge_drift_and_comma_safety():
    comma_charge = "BATTERY, DOMESTIC"
    bond_same = _valid_bond_data(charges=comma_charge)
    ok_report = evaluate_paperwork_preflight(
        bond_data=bond_same,
        arrest_doc={"charges": comma_charge},
        poa_doc={"max_bond_value": 15000.0},
    )
    assert ok_report["ready"] is True
    assert ok_report["checks"]["verbatim_charges"]["status"] == "pass"

    bond_drift = _valid_bond_data(charges="Domestic Battery")
    drift_report = evaluate_paperwork_preflight(
        bond_data=bond_drift,
        arrest_doc={"charges": comma_charge},
        poa_doc={"max_bond_value": 15000.0},
    )
    assert drift_report["ready"] is False
    codes = {i["code"] for i in drift_report["blocking_issues"]}
    assert "verbatim_charge_drift" in codes


def test_preflight_blocks_below_statutory_minimum_premium():
    # Two charges of $500 each -> min is $100 + $100 = $200
    charge_details = [
        {"charge": "DUI", "bond_amount": 500},
        {"charge": "DWLS", "bond_amount": 500},
    ]
    assert compute_fl_statutory_min_premium(1000.0, charge_details) == 200.0

    bond = _valid_bond_data(
        bond_amount=1000.0,
        premium_amount=100.0,
        charge_details=charge_details,
    )
    report = evaluate_paperwork_preflight(bond_data=bond, poa_doc={"max_bond_value": 5000.0})
    assert report["ready"] is False
    codes = {i["code"] for i in report["blocking_issues"]}
    assert "premium_below_statutory_minimum" in codes


def test_preflight_blocks_role_identity_dl_and_person_collision():
    dl_collision = _valid_bond_data()
    dl_collision["indemnitors"][0]["dl"] = dl_collision["defendant"]["dl"]
    rep_dl = evaluate_paperwork_preflight(
        bond_data=dl_collision,
        poa_doc={"max_bond_value": 15000.0},
        self_indemnitor=False,
    )
    assert rep_dl["ready"] is False
    assert "identity_dl_collision" in {i["code"] for i in rep_dl["blocking_issues"]}

    person_collision = _valid_bond_data()
    person_collision["indemnitors"][0]["name"] = "John Sample"
    person_collision["indemnitors"][0]["dob"] = "1991-05-12"
    rep_person = evaluate_paperwork_preflight(
        bond_data=person_collision,
        poa_doc={"max_bond_value": 15000.0},
        self_indemnitor=False,
    )
    assert rep_person["ready"] is False
    assert "identity_person_collision" in {i["code"] for i in rep_person["blocking_issues"]}

    # Self-indemnitor explicitly permits same DL & person
    rep_self = evaluate_paperwork_preflight(
        bond_data=dl_collision,
        poa_doc={"max_bond_value": 15000.0},
        self_indemnitor=True,
    )
    assert rep_self["checks"]["role_identity"]["status"] == "pass"


def test_preflight_blocks_poa_tier_shortfall_and_warns_defendant_omitted():
    bond = _valid_bond_data(bond_amount=50000.0, premium_amount=5000.0)
    report = evaluate_paperwork_preflight(
        bond_data=bond,
        poa_doc={"max_bond_value": 25000.0},
        include_defendant=False,
    )
    assert report["ready"] is False
    assert "docuseal_poa_tier_invalid" in {i["code"] for i in report["blocking_issues"]}
    assert "defendant_signing_omitted" in {w["code"] for w in report["warnings"]}


class _FakeCol:
    def __init__(self, doc=None):
        self.doc = doc

    async def find_one(self, *args, **kwargs):
        return self.doc


def test_preflight_endpoint_returns_structured_report():
    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)

    ctx = {
        "bond_case_id": "BC-API-1",
        "match_id": "M-API-1",
        "defendant_id": "D-API-1",
        "indemnitor_id": "I-API-1",
        "match_status": "validated",
        "surety_id": "osi",
        "poa_number": "OSI3 111222",
        "case_number": "26-CF-009001",
        "booking_number": "BK-API-1",
        "county": "Lee",
        "facility": "Lee County Jail",
        "court_date": "2026-12-20",
        "bond_amount": 5000.0,
        "premium_amount": 500.0,
        "charges": "BATTERY, DOMESTIC",
        "defendant": {
            "name": "Def One",
            "dob": "1990-01-01",
            "dl": "D1234567",
            "phone": "2395550111",
            "email": "def1@example.invalid",
        },
        "indemnitor": {
            "name": "Ind One",
            "dob": "1985-02-02",
            "dl": "I7654321",
            "phone": "2395550112",
            "email": "ind1@example.invalid",
        },
    }

    def fake_col(name: str):
        if name == "arrests":
            return _FakeCol({"charges": "BATTERY, DOMESTIC"})
        if name == "poa_inventory":
            return _FakeCol({"max_bond_value": 10000.0})
        return _FakeCol(None)

    with patch(
        "dashboard.services.packet_builder_service.resolve_case_context",
        new=AsyncMock(return_value=ctx),
    ), patch(
        "dashboard.routers.paperwork.get_collection",
        side_effect=fake_col,
    ), patch(
        "dashboard.services.surety_entitlements.entitlement_denial",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post(
            "/api/paperwork/preflight",
            json={"surety_id": "osi", "booking_number": "BK-API-1"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["preflight"]["ready"] is True
    assert payload["preflight"]["score"] == 100
