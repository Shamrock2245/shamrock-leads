"""Start bond packet. No text, no charge, no POA burn, no live DocuSeal."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.recovery_scope import path_allowed_for_recovery
from dashboard.routers.start_bond_packet import router
from dashboard.services import start_bond_packet as packet
from dashboard.tenancy.context import TenantContextMiddleware
from tests.test_tenant_scope import MemoryCollection

ROOT = Path(__file__).resolve().parents[1]


def _install(monkeypatch):
    arrests = MemoryCollection(
        [{"booking_number": "B-100", "full_name": "Ada Defendant", "county": "Lee", "state": "FL"}]
    )
    poa = MemoryCollection(
        [
            {"poa_number": "OSI-1", "surety_id": "osi", "status": "available", "tenant_id": "shamrock", "max_bond_value": 5000},
            {"poa_number": "OSI-2", "surety_id": "osi", "status": "used", "tenant_id": "shamrock"},
            {"poa_number": "OSI-9", "surety_id": "osi", "status": "available", "tenant_id": "gulf_coast_bail"},
        ]
    )
    bonds = MemoryCollection(
        [
            {
                "booking_number": "B-100",
                "tenant_id": "shamrock",
                "payment_link": "https://pay.example/invoice",
                "Bond_Case_ID": "BC-1",
                "Case_Number": "2026-CF-1",
                "Surety_ID": "osi",
                "POA_Number": "OSI-1",
                "Defendant_ID": "def-1",
                "Indemnitor_ID": "ind-1",
                "Match_ID": "match-1",
            }
        ]
    )
    tenants = MemoryCollection(
        [
            {"tenant_id": "shamrock", "legal_name": "Shamrock Bail Bonds"},
            {"tenant_id": "gulf_coast_bail", "surety_access": {"enabled": ["osi"], "private_templates": []}},
        ]
    )
    parties = {
        "defendants": MemoryCollection(
            [{"Defendant_ID": "def-1", "name": "Ada Defendant", "email": "ada@example.com", "tenant_id": "shamrock"}]
        ),
        "indemnitors": MemoryCollection(
            [{"Indemnitor_ID": "ind-1", "name": "Pat Indemnitor", "email": "pat@example.com", "tenant_id": "shamrock"}]
        ),
        "matches": MemoryCollection(
            [{"Match_ID": "match-1", "Status": "validated", "tenant_id": "shamrock"}]
        ),
        "paperwork_packets": MemoryCollection(),
        "bond_cases": MemoryCollection(),
    }
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "42")
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr(
        "dashboard.extensions._mongo_db",
        {"arrests": arrests, "poa_inventory": poa, "active_bonds": bonds, "tenants": tenants, **parties},
    )
    poa.packets = parties["paperwork_packets"]
    poa.bonds = bonds
    return poa


def _app():
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    return app


def _patch_providers(monkeypatch, *, preflight_state="eligible_for_staff_approval"):
    calls = {"preflight": 0, "submit": 0}

    async def preflight(**kwargs):
        calls["preflight"] += 1
        calls["preflight_kwargs"] = kwargs
        if preflight_state != "eligible_for_staff_approval":
            return {"state": "blocked", "block_reasons": ["missing_indemnitor_id"]}
        return {
            "state": "eligible_for_staff_approval",
            "block_reasons": [],
            "details": {"poa_number": "OSI-1", "bond_case_id": "BC-1", "surety_id": "osi"},
        }

    async def submit(**kwargs):
        calls["submit"] += 1
        calls["submit_kwargs"] = kwargs
        assert kwargs["send_email"] is False
        return {
            "submission_id": "sub-1",
            "signing_url": "https://sign.example/ignored",
            "submitters": [
                {"role": "Indemnitor", "sign_url": "https://sign.example/indemnitor"},
                {"role": "Defendant", "sign_url": "https://sign.example/defendant"},
            ],
        }

    monkeypatch.setattr(packet, "_default_preflight", preflight)
    monkeypatch.setattr(packet, "_default_submit", submit)
    return calls


def _body(**extra):
    body = {
        "booking_number": "B-100",
        "surety_id": "osi",
        "poa_number": "OSI-1",
        "confirmed": True,
        "indemnitor": {"name": "Pat Indemnitor", "email": "pat@example.com"},
    }
    body.update(extra)
    return body


def test_service_does_not_send_or_charge():
    text = Path(packet.__file__).read_text(encoding="utf-8")
    for banned in ("send_message_universal", "stripe", "smtplib", "swipesimple_invoice_service"):
        assert banned not in text
    bonds = (ROOT / "dashboard/routers/bonds.py").read_text(encoding="utf-8")
    assert "legacy_esign_retired" in bonds
    assert "status_code=410" in bonds
    assert path_allowed_for_recovery("/bond-packet", "GET") is False
    assert path_allowed_for_recovery("/api/bond-packet/send", "POST") is False


def test_flag_off_hides_the_flow(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    client = TestClient(_app())
    assert client.get("/bond-packet").status_code == 404
    assert client.post("/api/bond-packet/send", json=_body()).status_code == 404


def test_prepare_and_send_does_not_burn_the_power(monkeypatch):
    poa = _install(monkeypatch)
    calls = _patch_providers(monkeypatch)
    client = TestClient(_app())
    page = client.get("/bond-packet")
    assert page.status_code == 200
    assert "Look up defendant" in page.text
    assert 'id="done-links"' in page.text
    assert "row.sign_url" in page.text
    assert "result.body.pay_url" in page.text
    prepared = client.post("/api/bond-packet/prepare", json={"booking_number": "B-100", "surety_id": "osi"})
    assert prepared.status_code == 200
    body = prepared.json()
    assert body["defendant"]["defendant_name"] == "Ada Defendant"
    assert [row["poa_number"] for row in body["poa_suggestions"]] == ["OSI-1"]
    assert body["messages_sent"] == 0
    sent = client.post("/api/bond-packet/send", json=_body(template_id="999"))
    assert sent.status_code == 200
    result = sent.json()
    assert result["state"] == "ready_for_staff_send"
    assert result["sent"] is False
    assert result["messages_sent"] == 0
    assert result["poa_status"] == "available"
    assert result["signing_url"] == "https://sign.example/indemnitor"
    assert result["signing_url"] != "https://sign.example/ignored"
    assert [row["sign_url"] for row in result["sign_links"]] == [
        "https://sign.example/indemnitor",
        "https://sign.example/defendant",
    ]
    assert result["pay_url"] == "https://pay.example/invoice"
    assert calls["submit"] == 1
    sent_data = calls["submit_kwargs"]["bond_data"]
    for key in (
        "bond_case_id",
        "match_id",
        "match_status",
        "defendant_id",
        "indemnitor_id",
        "case_number",
        "poa_number",
        "booking_number",
        "surety_id",
    ):
        assert sent_data[key]
    assert sent_data["match_status"] == "validated"
    assert sent_data["poa_number"] == "OSI-1"
    assert calls["submit_kwargs"]["template_id"] == "42"
    assert calls["submit_kwargs"]["defendant"]["email"] == "ada@example.com"
    assert calls["submit_kwargs"]["indemnitors"][0]["email"] == "pat@example.com"
    assert poa.docs[0]["status"] == "available"
    assert len(poa.packets.docs) == 1
    stored = poa.packets.docs[0]
    assert stored["packet_id"] == "BC-1"
    assert stored["bond_case_id"] == "BC-1"
    assert stored["docuseal_submission_id"] == "sub-1"
    assert stored["docuseal_template_id"] == "42"
    assert stored["tenant_id"] == "shamrock"
    created_at = stored["created_at"]
    again = client.post("/api/bond-packet/send", json=_body(template_id="999"))
    assert again.status_code == 200
    assert len(poa.packets.docs) == 1
    assert poa.packets.docs[0]["created_at"] == created_at
    assert "find_one" not in Path(packet.__file__).read_text(encoding="utf-8").split("async def _store_packet", 1)[1].split("async def send_packet", 1)[0]
    assert "upsert=True" in Path(packet.__file__).read_text(encoding="utf-8")
    unconfirmed = client.post("/api/bond-packet/send", json=_body(confirmed=False))
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["error"] == "staff_confirmation_required"


def test_blocked_preflight_does_not_create_a_submission(monkeypatch):
    _install(monkeypatch)
    calls = _patch_providers(monkeypatch, preflight_state="blocked")
    client = TestClient(_app())
    sent = client.post("/api/bond-packet/send", json=_body())
    assert sent.status_code == 409
    assert sent.json()["sent"] is False
    assert calls["submit"] == 0


def test_poa_must_match_preflight(monkeypatch):
    _install(monkeypatch)

    async def preflight(**kwargs):
        return {
            "state": "eligible_for_staff_approval",
            "block_reasons": [],
            "details": {"poa_number": "OSI-2", "bond_case_id": "BC-1"},
        }

    async def submit(**kwargs):
        raise AssertionError("submission created")

    monkeypatch.setattr(packet, "_default_preflight", preflight)
    monkeypatch.setattr(packet, "_default_submit", submit)
    sent = TestClient(_app()).post("/api/bond-packet/send", json=_body(template_id="999"))
    assert sent.status_code == 400
    assert sent.json()["error"] == "poa_not_available"


def test_other_tenant_does_not_receive_shamrock_template(monkeypatch):
    poa = _install(monkeypatch)
    monkeypatch.setenv("SURETY_TEMPLATE_STORE", "memory")
    poa.bonds.docs.append(
        {
            "booking_number": "B-100",
            "tenant_id": "gulf_coast_bail",
            "Bond_Case_ID": "BC-9",
            "Case_Number": "2026-CF-9",
            "Surety_ID": "osi",
            "POA_Number": "OSI-9",
            "Defendant_ID": "def-9",
            "Indemnitor_ID": "ind-9",
            "Match_ID": "match-9",
        }
    )
    from dashboard.extensions import _mongo_db

    _mongo_db["defendants"].docs.append(
        {"Defendant_ID": "def-9", "name": "Ada Defendant", "email": "ada@example.com", "tenant_id": "gulf_coast_bail"}
    )
    _mongo_db["indemnitors"].docs.append(
        {"Indemnitor_ID": "ind-9", "name": "Pat Indemnitor", "email": "pat@example.com", "tenant_id": "gulf_coast_bail"}
    )
    _mongo_db["matches"].docs.append(
        {"Match_ID": "match-9", "Status": "validated", "tenant_id": "gulf_coast_bail"}
    )
    calls = _patch_providers(monkeypatch)

    async def preflight(**kwargs):
        return {
            "state": "eligible_for_staff_approval",
            "details": {"poa_number": "OSI-9", "bond_case_id": "BC-9"},
        }

    monkeypatch.setattr(packet, "_default_preflight", preflight)
    app = _app()

    @app.middleware("http")
    async def stamp(request, call_next):
        request.state.sl_email = "admin@shamrockbailbonds.biz"
        request.state.sl_role = "god_admin"
        return await call_next(request)

    sent = TestClient(app).post(
        "/api/bond-packet/send",
        json=_body(poa_number="OSI-9", template_id="42"),
        headers={"X-Tenant-Id": "gulf_coast_bail"},
    )
    assert sent.status_code == 400
    assert sent.json()["error"] == "template_unavailable"
    assert calls["submit"] == 0


def test_other_tenant_does_not_see_shamrock_powers(monkeypatch):
    _install(monkeypatch)
    _patch_providers(monkeypatch)
    app = _app()

    @app.middleware("http")
    async def stamp(request, call_next):
        request.state.sl_email = "admin@shamrockbailbonds.biz"
        request.state.sl_role = "god_admin"
        return await call_next(request)

    client = TestClient(app)
    prepared = client.post(
        "/api/bond-packet/prepare",
        json={"booking_number": "B-100", "surety_id": "osi"},
        headers={"X-Tenant-Id": "gulf_coast_bail"},
    )
    assert prepared.status_code == 200
    assert [row["poa_number"] for row in prepared.json()["poa_suggestions"]] == ["OSI-9"]
