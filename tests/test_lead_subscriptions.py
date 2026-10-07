"""Lead subscriptions. No production database and no scraper run."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.platform_leads import router
from dashboard.services.lead_subscriptions import (
    fan_out_if_enabled,
    sellable_counties,
    subscribers_for,
)
from tests.test_tenant_scope import MemoryCollection


def _run(coro):
    return asyncio.run(coro)


class SyncCol:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    def find(self, filt=None, projection=None):
        return list(self.docs)

    def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (filt or {}).items()):
                return
        if upsert:
            self.docs.append(dict(update.get("$setOnInsert") or {}))


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


class _Arrest:
    def __init__(self, county, booking, name="Hidden Person"):
        self.State = "FL"
        self.County = county
        self.Booking_Number = booking
        self.Full_Name = name


def test_fail_closed_counties_are_not_sellable():
    labels = {row["label"] for row in sellable_counties()}
    assert "Lee (FL)" in labels
    assert "Clermont (OH)" not in labels
    assert "Clinton (OH)" not in labels
    assert "Huron (OH)" not in labels


def test_flag_off_does_not_route_or_open_the_console(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)

    class Boom(dict):
        def __getitem__(self, key):
            raise AssertionError("writer touched")

    assert fan_out_if_enabled(Boom(), [_Arrest("Lee", "B1")]) == []
    assert _client().get("/platform/leads").status_code == 404


def test_shared_default_and_exclusive_rejection(monkeypatch):
    _install(monkeypatch)
    client = _client()
    page = client.get("/platform/leads")
    assert page.status_code == 200
    assert "Ohio" in page.text
    listing = client.get("/api/platform/lead-subscriptions?state=FL").json()
    assert "OH" not in listing["states"] or "Clermont (OH)" not in {row["label"] for row in listing["counties"]}
    shamrock = next(row for row in listing["agencies"] if row["tenant_id"] == "shamrock")
    assert shamrock["seeded"] is True
    lee = next(item for item in shamrock["subscriptions"] if item["county"] == "Lee")
    assert lee["state"] == "FL"
    assert lee["mode"] == "shared"
    assert lee["price_cents"] is None
    assert "OH" not in listing["states"]
    blocked = client.put(
        "/api/platform/tenants/gulf_coast_bail/lead-subscriptions",
        json={"subscriptions": [{"state": "OH", "county": "Clermont", "mode": "shared"}]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"] == "not_sellable"
    exclusive = client.put(
        "/api/platform/tenants/gulf_coast_bail/lead-subscriptions",
        json={"subscriptions": [{"state": "FL", "county": "Lee", "mode": "exclusive"}]},
    )
    assert exclusive.status_code == 400
    assert exclusive.json()["error"] == "exclusive_taken"
    shared = client.put(
        "/api/platform/tenants/gulf_coast_bail/lead-subscriptions",
        json={"subscriptions": [{"state": "FL", "county": "Lee", "mode": "shared"}]},
    )
    assert shared.status_code == 200
    assert shared.json()["subscriptions"][0]["price_cents"] is None
    assert shared.json()["subscriptions"][0]["mode"] == "shared"


def test_route_lead_writes_pointers_without_pii(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    tenants = SyncCol(
        [
            {"tenant_id": "shamrock"},
            {
                "tenant_id": "gulf_coast_bail",
                "lead_subscriptions": [{"state": "FL", "county": "Lee", "mode": "shared", "price_cents": None}],
            },
        ]
    )
    leads = SyncCol()
    db = {"tenants": tenants, "leads": leads}
    routed = fan_out_if_enabled(db, [_Arrest("Lee", "B-100", name="Hidden Person")])
    assert routed.count("shamrock") == 1
    assert routed.count("gulf_coast_bail") == 1
    assert subscribers_for(db, state="OH", county="Clermont") == []
    dumped = json.dumps(leads.docs)
    assert "Hidden Person" not in dumped
    assert "B-100" in dumped
    again = fan_out_if_enabled(db, [_Arrest("Lee", "B-100")])
    assert again
    assert len(leads.docs) == 2
