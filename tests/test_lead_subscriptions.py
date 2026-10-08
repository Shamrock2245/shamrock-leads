"""Lead subscriptions. No production database and no scraper run."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.platform_leads import router
from pymongo.errors import DuplicateKeyError

from dashboard.services.lead_subscriptions import (
    LEAD_COUNTY_CLAIMS,
    fan_out_if_enabled,
    record_lead_fanout,
    retry_lead_fanout,
    sellable_counties,
    shamrock_seed,
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


def _install(monkeypatch, claims=None):
    tenants = MemoryCollection(
        [
            {"tenant_id": "shamrock", "legal_name": "Shamrock Bail Bonds"},
            {"tenant_id": "gulf_coast_bail", "legal_name": "Gulf Coast Bail"},
        ]
    )
    claims = claims or MemoryCollection()
    audits = MemoryCollection()
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr(
        "dashboard.extensions._mongo_db",
        {"tenants": tenants, LEAD_COUNTY_CLAIMS: claims, "audit_events": audits},
    )
    tenants.claims = claims
    tenants.audits = audits
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
    assert "row.price_cents = existing.price_cents" in page.text


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


class _Outbox:
    def __init__(self):
        self.docs = []

    def find(self, filt=None, projection=None):
        rows = []
        for doc in self.docs:
            if filt and any(doc.get(key) != value for key, value in filt.items()):
                continue
            rows.append(doc)
        return rows

    def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (filt or {}).items()):
                doc.update(update.get("$set") or {})
                return
        if upsert:
            row = dict(update.get("$setOnInsert") or {})
            row.update(update.get("$set") or {})
            self.docs.append(row)


class _FlakyLeads(SyncCol):
    def __init__(self):
        super().__init__()
        self.failures = 1

    def update_one(self, filt, update, upsert=False):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("tenant lead write failed")
        return super().update_one(filt, update, upsert=upsert)


def _lee_db(leads, outbox):
    tenants = SyncCol(
        [
            {"tenant_id": "shamrock"},
            {
                "tenant_id": "gulf_coast_bail",
                "lead_subscriptions": [{"state": "FL", "county": "Lee", "mode": "shared", "price_cents": None}],
            },
        ]
    )
    return {"tenants": tenants, "leads": leads, "lead_fanout_outbox": outbox}


def test_failed_tenant_write_is_queued_and_retried(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    leads = _FlakyLeads()
    outbox = _Outbox()
    db = _lee_db(leads, outbox)
    routed = fan_out_if_enabled(db, [_Arrest("Lee", "B-200", name="Hidden Person")])
    assert routed == ["gulf_coast_bail"]
    assert len(outbox.docs) == 1
    queued = json.dumps(outbox.docs)
    assert "Hidden Person" not in queued
    assert "B-200" in queued
    assert outbox.docs[0]["status"] == "pending"
    assert retry_lead_fanout(db) == 1
    assert {doc["tenant_id"] for doc in leads.docs} == {"shamrock", "gulf_coast_bail"}
    assert outbox.docs[0]["status"] == "delivered"
    assert retry_lead_fanout(db) == 0
    assert "Hidden Person" not in json.dumps(leads.docs)


def test_writer_backstop_queues_when_fanout_raises(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    outbox = _Outbox()

    def boom(_db, _records):
        raise RuntimeError("fan-out down")

    monkeypatch.setattr("dashboard.services.lead_subscriptions.fan_out_if_enabled", boom)
    db = {"lead_fanout_outbox": outbox}
    assert record_lead_fanout(db, [_Arrest("Lee", "B-300", name="Hidden Person")]) == []
    assert outbox.docs[0]["booking_number"] == "B-300"
    assert outbox.docs[0]["status"] == "pending"
    assert "Hidden Person" not in json.dumps(outbox.docs)


def test_total_fanout_failure_still_queues_the_pointer(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")

    class BoomTenants:
        def find(self, *_args, **_kwargs):
            raise RuntimeError("tenants down")

    outbox = _Outbox()
    db = {"tenants": BoomTenants(), "lead_fanout_outbox": outbox}
    assert fan_out_if_enabled(db, [_Arrest("Lee", "B-9", name="Hidden Person")]) == []
    assert outbox.docs[0]["status"] == "pending"
    assert "Hidden Person" not in json.dumps(outbox.docs)


class _RacingClaims(MemoryCollection):
    def __init__(self, county: str):
        super().__init__()
        self.county = county

    async def insert_one(self, doc, *args, **kwargs):
        if doc.get("state") == "FL" and doc.get("county") == self.county:
            self.docs.append(
                {
                    "state": "FL",
                    "county": self.county,
                    "mode": "exclusive",
                    "holders": ["other_agency"],
                }
            )
            raise DuplicateKeyError("lead_county_claim_unique")
        return await super().insert_one(doc, *args, **kwargs)


def _open_county() -> str:
    seed = {row["county"].lower() for row in shamrock_seed()}
    for row in sellable_counties():
        if row["state"] == "FL" and row["county"].lower() not in seed:
            return row["county"]
    raise AssertionError("no sellable Florida county outside the Shamrock seed")


def test_exclusive_claim_is_unique_and_a_duplicate_key_is_rejected(monkeypatch):
    county = _open_county()
    claims = _RacingClaims(county)
    tenants = _install(monkeypatch, claims=claims)
    client = _client()
    exclusive = client.put(
        "/api/platform/tenants/gulf_coast_bail/lead-subscriptions",
        json={"subscriptions": [{"state": "FL", "county": county, "mode": "exclusive"}]},
    )
    assert exclusive.status_code == 400
    assert exclusive.json()["error"] == "exclusive_taken"
    assert claims.indexes
    keys, opts = claims.indexes[0]
    assert list(keys[0]) == [("state", 1), ("county", 1)]
    assert opts["unique"] is True
    assert opts["name"] == "lead_county_claim_unique"
    stored = next(doc for doc in tenants.docs if doc["tenant_id"] == "gulf_coast_bail")
    assert "lead_subscriptions" not in stored


def test_subscription_save_writes_an_audit_row(monkeypatch):
    tenants = _install(monkeypatch)
    client = _client()
    saved = client.put(
        "/api/platform/tenants/gulf_coast_bail/lead-subscriptions",
        json={
            "reason": "desk assignment",
            "subscriptions": [{"state": "FL", "county": "Lee", "mode": "shared", "price_cents": 2500}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["subscriptions"][0]["price_cents"] == 2500
    assert len(tenants.audits.docs) == 1
    event = tenants.audits.docs[0]
    assert event["tenant_id"] == "gulf_coast_bail"
    assert event["entity_type"] == "tenant_lead_subscriptions"
    assert event["action"] == "lead_subscriptions_updated"
    assert event["actor"] == "admin@shamrockbailbonds.biz"
    assert event["reason"] == "desk assignment"
    assert event["old_state"]["subscriptions"] == []
    assert event["new_state"]["subscriptions"][0]["county"] == "Lee"
    assert event["new_state"]["subscriptions"][0]["price_cents"] == 2500
    assert event["event_id"]
