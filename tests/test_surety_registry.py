"""Surety registry: OSI + Palmetto active; four carriers listed inactive; fail closed."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from dashboard.services import surety_registry as sr


def test_active_and_inactive_lists():
    assert sr.SUPPORTED_SURETIES == ("osi", "palmetto")
    inactive = [k for k, v in sr.SURETY_REGISTRY.items() if not v["active"]]
    assert inactive == ["lexington", "roche", "universal", "bankers"]


@pytest.mark.parametrize("raw", ["lexington", "Lexington National", "roche", "Universal", "bankers surety"])
def test_inactive_sureties_fail_closed(raw):
    assert sr.is_known_surety(raw)
    assert not sr.is_supported_surety(raw)
    with pytest.raises(sr.UnsupportedSuretyError) as ei:
        sr.require_surety(raw)
    assert ei.value.code == "surety_inactive"
    assert sr.template_id_for(raw) is None
    assert sr.drive_folder_label(raw) is None


def test_inactive_never_gets_template_even_if_env_set():
    with patch.dict(os.environ, {"DOCUSEAL_TEMPLATE_ID_LEXINGTON": "99"}):
        assert sr.template_id_for("lexington") is None


@pytest.mark.parametrize("raw", ["acme bail", "osi2", "o.s.i."])
def test_unknown_surety_fails_closed(raw):
    assert not sr.is_supported_surety(raw)
    with pytest.raises(sr.UnsupportedSuretyError) as ei:
        sr.require_surety(raw)
    assert ei.value.code == "unsupported_surety"


def test_missing_surety_required_at_packet_time_but_optional_at_intake():
    assert sr.optional_surety("") is None
    with pytest.raises(sr.UnsupportedSuretyError) as ei:
        sr.require_surety(None)
    assert ei.value.code == "surety_required"


def test_template_ids_active():
    env = {"DOCUSEAL_TEMPLATE_ID_OSI": "1", "DOCUSEAL_TEMPLATE_ID_PALMETTO": "5"}
    with patch.dict(os.environ, env):
        assert sr.template_id_for("OSI") == "1"
        assert sr.template_id_for("palmetto") == "5"
    with patch.dict(os.environ, {"DOCUSEAL_TEMPLATE_ID_PALMETTO": "", "DOCUSEAL_TEMPLATE_ID": "1"}):
        # Palmetto never falls back to the OSI template.
        assert sr.template_id_for("palmetto") is None


def test_docuseal_resolver_delegates_to_registry():
    from dashboard.services.docuseal_service import resolve_template_id_for_surety

    with patch.dict(os.environ, {"DOCUSEAL_TEMPLATE_ID_OSI": "1"}):
        assert resolve_template_id_for_surety("osi") == "1"
        assert resolve_template_id_for_surety(None) is None
        assert resolve_template_id_for_surety("roche") is None
        assert resolve_template_id_for_surety("unknown") is None


def test_picker_options_grey_out_inactive():
    rows = {r["id"]: r for r in sr.picker_options()}
    assert rows["osi"]["selectable"] and rows["palmetto"]["selectable"]
    for sid in ("lexington", "roche", "universal", "bankers"):
        assert rows[sid]["selectable"] is False
        assert rows[sid]["status"] == "coming_soon"
        assert rows[sid]["website"].startswith("https://")


def test_sureties_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    r = TestClient(app).get("/api/paperwork/sureties")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["sureties"]]
    assert ids[:2] == ["osi", "palmetto"] and "bankers" in ids


def test_appearance_bond_data_rejects_inactive_surety():
    from dashboard.routers.bonds import _build_appearance_bond_data

    data, err = _build_appearance_bond_data({
        "booking_number": "1", "charge": "X", "bond": 100, "surety": "lexington",
    })
    assert data == {} and err
