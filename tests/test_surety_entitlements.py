"""Per-agency surety checklist. No packet rendering, no production database."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.platform_sureties import router
from dashboard.services.surety_entitlements import assert_entitled, entitled_ids
from dashboard.services.surety_registry import picker_options, require_surety
from dashboard.tenancy.context import bind_job_tenant
from tests.test_tenant_scope import MemoryCollection


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch):
    tenants = MemoryCollection(
        [
            {"tenant_id": "shamrock", "legal_name": "Shamrock Bail Bonds"},
            {"tenant_id": "gulf_coast_bail", "legal_name": "Gulf Coast Bail"},
        ]
    )
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr("dashboard.extensions._mongo_db", {"tenants": tenants})
    return tenants


def _client():
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request, call_next):
        request.state.sl_email = "admin@shamrockbailbonds.biz"
        request.state.sl_role = "god_admin"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_flag_off_keeps_todays_registry(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    monkeypatch.setattr(
        "dashboard.extensions.get_mongo_client",
        lambda: (_ for _ in ()).throw(AssertionError("mongo touched")),
    )
    assert require_surety("palmetto") == "palmetto"
    assert _run(assert_entitled("osi")) is None
    rows = picker_options()
    assert rows[0]["id"] == "osi" and rows[0]["selectable"] is True
    inactive = next(row for row in rows if row["id"] == "lexington")
    assert inactive["selectable"] is False
    client = _client()
    assert client.get("/platform/sureties").status_code == 404
    assert client.put("/api/platform/tenants/gulf_coast_bail/sureties", json={"enabled": ["osi"]}).status_code == 404


def test_shamrock_keeps_osi_and_palmetto_and_inactive_stays_off(monkeypatch):
    _install(monkeypatch)
    client = _client()
    page = client.get("/platform/sureties")
    assert page.status_code == 200
    assert "not available yet" in page.text
    listing = client.get("/api/platform/sureties").json()
    shamrock = next(row for row in listing["agencies"] if row["tenant_id"] == "shamrock")
    assert shamrock["enabled"] == ["osi", "palmetto"] or set(shamrock["enabled"]) >= {"osi", "palmetto"}
    blocked = client.put(
        "/api/platform/tenants/gulf_coast_bail/sureties",
        json={"enabled": ["lexington"]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"] == "surety_inactive"
    saved = client.put(
        "/api/platform/tenants/gulf_coast_bail/sureties",
        json={"enabled": ["palmetto"], "private_templates": [{"label": "Gulf rider"}]},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["enabled"] == ["palmetto"]
    assert body["private_templates"] == [{"id": "private_1", "label": "Gulf rider", "status": "slot"}]
    locked = client.put(
        "/api/platform/tenants/shamrock/sureties",
        json={"enabled": []},
    )
    assert locked.status_code == 200
    assert set(locked.json()["enabled"]) >= {"osi", "palmetto"}


def test_unenforced_surety_cannot_generate(monkeypatch):
    _install(monkeypatch)

    async def allowed():
        with bind_job_tenant("shamrock"):
            await assert_entitled("osi")
            await assert_entitled("palmetto")
            return sorted(await entitled_ids())

    assert _run(allowed()) == ["osi", "palmetto"]

    async def denied():
        from dashboard.services.docuseal_service import (
            DocuSealPacketValidationError,
            DocuSealService,
        )
        from dashboard.services.surety_entitlements import SuretyEntitlementError, annotate_picker

        with bind_job_tenant("gulf_coast_bail"):
            rows = {row["id"]: row for row in await annotate_picker(picker_options())}
            assert rows["palmetto"]["selectable"] is False
            assert rows["osi"]["reason"] == "Not enabled for this agency"
            try:
                await assert_entitled("palmetto")
            except SuretyEntitlementError as exc:
                code = exc.code
            else:
                code = ""
            try:
                await DocuSealService.create_submission_for_packet(
                    object(),
                    template_id=1,
                    packet_id="pkt",
                    bond_data={"surety_id": "osi"},
                )
            except DocuSealPacketValidationError as exc:
                packet_code = str(exc)
            else:
                packet_code = ""
            return code, packet_code

    assert _run(denied()) == ("surety_not_entitled", "surety_not_entitled")
