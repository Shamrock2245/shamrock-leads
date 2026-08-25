"""Guards for the live paperwork E2E mock packet."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from dashboard.services.docuseal_service import validate_docuseal_packet_binding


def _load_e2e_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "e2e_test_paperwork.py"
    spec = importlib.util.spec_from_file_location("e2e_test_paperwork", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_mock_bond_data_passes_packet_binding_for_all_roles():
    mod = _load_e2e_module()
    bond = mod._bond_data("osi")
    validate_docuseal_packet_binding(
        packet_id=bond["packet_id"],
        bond_data=bond,
        indemnitors=bond["indemnitors"],
        defendant=bond["defendant"],
        include_defendant=True,
    )
    roles = {party["name"] for party in bond["indemnitors"]}
    assert mod.E2E_INDEMNITOR_NAME in roles
    assert mod.E2E_COINDEMNITOR_NAME in roles
    assert bond["include_bondsman"] is True
    assert bond["bondsman_name"] == mod.E2E_BONDSMAN_NAME
    assert bond["defendant"]["email"].endswith("@shamrockbailbonds.biz")
    assert bond["match_status"] == "validated"
    assert bond["surety_id"] == "osi"


def test_prefill_includes_coindemnitor_on_indemnitor_header():
    from dashboard.services.docuseal_service import DocuSealService

    mod = _load_e2e_module()
    bond = mod._bond_data("osi")
    vals = DocuSealService(base_url="https://sign.example", api_key="test").prefill_values_from_bond(bond)
    assert mod.E2E_INDEMNITOR_NAME in vals["indemnitor_name"]
    assert mod.E2E_COINDEMNITOR_NAME in vals["indemnitor_name"]
    assert vals["coindemnitor_name"] == mod.E2E_COINDEMNITOR_NAME
    assert vals["defendant_name"] == mod.E2E_DEFENDANT_NAME
    assert "Brendan" in (vals.get("agent_name") or bond["bondsman_name"])
