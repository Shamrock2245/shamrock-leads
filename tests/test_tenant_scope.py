"""Tenant chokepoint tests. No production database, no network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.tenancy.constants import (
    AUDIT_TTL_CURRENT_SECONDS,
    GLOBAL_COLLECTIONS,
    KNOWN_APP_COLLECTIONS,
    SHAMROCK_TENANT_ID,
    TENANT_OWNED_COLLECTIONS,
)
from dashboard.tenancy.context import (
    bind_job_tenant,
    bind_platform_job,
    current_tenant_id,
    resolve_tenant_id,
)
from dashboard.tenancy.indexes import audit_retention_policy, tenant_index_specs
from dashboard.tenancy.scope import TenantScopeError, TenantScopedCollection

ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


class MemoryCollection:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]
        self.filters = []
        self.pipelines = []

    def _match(self, doc, filt):
        if not filt:
            return True
        if "$or" in filt and len(filt) == 1:
            return any(self._match(doc, item) for item in filt["$or"])
        for key, expected in filt.items():
            if key == "$or":
                if not any(self._match(doc, item) for item in expected):
                    return False
                continue
            if isinstance(expected, dict) and "$exists" in expected:
                present = key in doc and doc.get(key) not in (None, "")
                if bool(expected["$exists"]) != present:
                    return False
                continue
            if isinstance(expected, dict) and "$ne" in expected:
                if doc.get(key) == expected["$ne"]:
                    return False
                continue
            if doc.get(key) != expected:
                return False
        return True

    def _rows(self, filt):
        self.filters.append(filt)
        return [doc for doc in self.docs if self._match(doc, filt or {})]

    async def find_one(self, filt=None, *args, **kwargs):
        rows = self._rows(filt)
        return dict(rows[0]) if rows else None

    def find(self, filt=None, *args, **kwargs):
        return list(self._rows(filt))

    async def insert_one(self, doc, *args, **kwargs):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="1")

    async def insert_many(self, docs, *args, **kwargs):
        for doc in docs:
            self.docs.append(dict(doc))
        return SimpleNamespace(inserted_ids=["1"])

    async def update_one(self, filt, update, *args, **kwargs):
        for doc in self.docs:
            if self._match(doc, filt or {}):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def update_many(self, filt, update, *args, **kwargs):
        count = 0
        for doc in self.docs:
            if self._match(doc, filt or {}):
                doc.update(update.get("$set", {}))
                count += 1
        return SimpleNamespace(modified_count=count)

    async def delete_one(self, filt, *args, **kwargs):
        for doc in list(self.docs):
            if self._match(doc, filt or {}):
                self.docs.remove(doc)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, filt, *args, **kwargs):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not self._match(doc, filt or {})]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, filt=None, *args, **kwargs):
        return len(self._rows(filt))

    async def aggregate(self, pipeline, *args, **kwargs):
        self.pipelines.append(pipeline)
        return []

    def estimated_document_count(self):
        raise AssertionError("estimated_document_count leaks across tenants")

    def create_index(self, *args, **kwargs):
        return "ok"


def _scoped(name, docs=None):
    raw = MemoryCollection(docs)
    return TenantScopedCollection(raw, name), raw


def test_known_collection_split_is_fail_closed():
    assert "arrests" in GLOBAL_COLLECTIONS
    assert "active_bonds" in TENANT_OWNED_COLLECTIONS
    assert "poa_inventory" in TENANT_OWNED_COLLECTIONS
    assert "notifications" in TENANT_OWNED_COLLECTIONS
    assert GLOBAL_COLLECTIONS.isdisjoint(TENANT_OWNED_COLLECTIONS)
    assert TENANT_OWNED_COLLECTIONS <= KNOWN_APP_COLLECTIONS
    assert len(KNOWN_APP_COLLECTIONS) == 107
    assert len(GLOBAL_COLLECTIONS) == 11


def test_flag_off_leaves_filters_and_documents_unchanged(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    scoped, raw = _scoped("active_bonds", [{"booking_number": "1", "tenant_id": "other"}])

    async def go():
        found = await scoped.find_one({"booking_number": "1"})
        await scoped.insert_one({"booking_number": "2"})
        return found

    found = _run(go())
    assert found["tenant_id"] == "other"
    assert raw.filters == [{"booking_number": "1"}]
    assert "tenant_id" not in raw.docs[-1]


def test_flag_off_get_collection_is_the_raw_object(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    sentinel = object()
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr("dashboard.extensions._mongo_db", {"notifications": sentinel})
    from dashboard.extensions import get_collection, get_db

    assert get_db()["notifications"] is sentinel
    assert get_collection("notifications") is sentinel


def test_cross_tenant_reads_and_writes_are_impossible(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    scoped, raw = _scoped(
        "active_bonds",
        [
            {"bond_case_id": "A", "tenant_id": "shamrock", "poa_number": "P-1"},
            {"bond_case_id": "B", "tenant_id": "other_agency", "poa_number": "P-2"},
        ],
    )

    async def go():
        with bind_job_tenant("shamrock"):
            visible = await scoped.find_one({"bond_case_id": "B"})
            own = await scoped.find_one({"bond_case_id": "A"})
            updated = await scoped.update_one(
                {"bond_case_id": "B"},
                {"$set": {"poa_number": "STOLEN"}},
            )
            deleted = await scoped.delete_many({})
            await scoped.insert_one({"bond_case_id": "C", "poa_number": "P-3"})
        return visible, own, updated, deleted

    visible, own, updated, deleted = _run(go())
    assert visible is None
    assert own["poa_number"] == "P-1"
    assert updated.modified_count == 0
    assert deleted.deleted_count == 1
    remaining = {doc["bond_case_id"] for doc in raw.docs}
    assert remaining == {"B", "C"}
    assert raw.docs[-1]["tenant_id"] == "shamrock"
    assert all(doc["poa_number"] != "STOLEN" for doc in raw.docs)


def test_explicit_cross_tenant_filter_and_move_are_rejected(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    scoped, raw = _scoped("payments", [{"payment_id": "1", "tenant_id": "shamrock"}])

    async def go():
        with bind_job_tenant("shamrock"):
            with pytest.raises(TenantScopeError):
                await scoped.find_one({"tenant_id": "other_agency"})
            with pytest.raises(TenantScopeError):
                await scoped.update_one({}, {"$set": {"tenant_id": "other_agency"}})
            with pytest.raises(TenantScopeError):
                await scoped.update_one({}, {"$unset": {"tenant_id": ""}})
            with pytest.raises(TenantScopeError):
                await scoped.find_one({"$where": "this.tenant_id"})
            with pytest.raises(TenantScopeError):
                await scoped.insert_one({"payment_id": "2", "tenant_id": "other_agency"})
            with pytest.raises(TenantScopeError):
                await scoped.aggregate([{"$out": "leak"}])
            with pytest.raises(TenantScopeError):
                scoped.bulk_write([])

    _run(go())
    assert raw.docs == [{"payment_id": "1", "tenant_id": "shamrock"}]


def test_aggregate_is_pinned_and_global_roster_is_not(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    payments, _raw = _scoped("payments")
    arrests = MemoryCollection([{"booking_number": "public"}])

    async def go():
        with bind_job_tenant("shamrock"):
            await payments.aggregate([{"$project": {"_id": 0}}])
        with bind_platform_job("scraper_lee"):
            # Global collection is returned raw by the database proxy.
            from dashboard.tenancy.scope import apply_scope

            raw = apply_scope(arrests, "arrests")
            assert raw is arrests
            with pytest.raises(TenantScopeError):
                await payments.find_one({})

    _run(go())
    assert payments._raw.pipelines[0][0] == {"$match": {"tenant_id": "shamrock"}}


def test_missing_tenant_fails_closed(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    scoped, _raw = _scoped("indemnitors")

    async def go():
        with pytest.raises(TenantScopeError) as caught:
            await scoped.find_one({})
        return caught.value.code

    assert _run(go()) == "tenant_required"


def test_flag_on_database_proxy_scopes_only_tenant_collections(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    notes = MemoryCollection()
    arrests = MemoryCollection()
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr(
        "dashboard.extensions._mongo_db",
        {"notifications": notes, "arrests": arrests},
    )
    from dashboard.extensions import get_collection
    from dashboard.tenancy.scope import TenantScopedCollection, TenantScopedDatabase
    from dashboard.extensions import get_db

    db = get_db()
    assert isinstance(db, TenantScopedDatabase)
    assert isinstance(get_collection("notifications"), TenantScopedCollection)
    assert get_collection("arrests") is arrests


def test_notification_slice_stamps_only_when_flag_on(monkeypatch):
    notes = MemoryCollection()
    monkeypatch.setattr("dashboard.extensions.get_mongo_client", lambda: object())
    monkeypatch.setattr("dashboard.extensions._mongo_db", {"notifications": notes})

    from dashboard.routers.notifications import create_notification

    async def off():
        return await create_notification("system", "Desk", "Flag off")

    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    off_doc = _run(off())
    assert "tenant_id" not in off_doc
    assert "tenant_id" not in notes.docs[0]

    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")

    async def on():
        with bind_job_tenant("shamrock"):
            return await create_notification("system", "Desk", "Flag on")

    on_doc = _run(on())
    assert on_doc["tenant_id"] == SHAMROCK_TENANT_ID
    assert notes.docs[1]["tenant_id"] == SHAMROCK_TENANT_ID

    async def other_cannot_see():
        with bind_job_tenant("other_agency"):
            scoped = TenantScopedCollection(notes, "notifications")
            return await scoped.count_documents({"title": "Desk"})

    # The second insert was stamped shamrock. The first has no tenant_id.
    assert _run(other_cannot_see()) == 0


def test_request_resolution_flag_off_ignores_hostile_host(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    assert (
        resolve_tenant_id(
            host="evil.example",
            session_tenant="other_agency",
            header_tenant="other_agency",
            is_platform_admin=False,
        )
        == SHAMROCK_TENANT_ID
    )


def test_request_resolution_flag_on(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    assert (
        resolve_tenant_id(
            host="leads.shamrockbailbonds.biz",
            session_tenant=None,
            header_tenant="other_agency",
            is_platform_admin=False,
        )
        == SHAMROCK_TENANT_ID
    )
    assert (
        resolve_tenant_id(
            host="acme.app.shamrockbailbonds.biz",
            session_tenant=None,
            header_tenant=None,
            is_platform_admin=False,
        )
        == "acme"
    )
    assert (
        resolve_tenant_id(
            host="evil.example",
            session_tenant=None,
            header_tenant=None,
            is_platform_admin=False,
        )
        is None
    )
    assert (
        resolve_tenant_id(
            host="leads.shamrockbailbonds.biz",
            session_tenant=SHAMROCK_TENANT_ID,
            header_tenant="other_agency",
            is_platform_admin=True,
        )
        == "other_agency"
    )
    assert (
        resolve_tenant_id(
            host="leads.shamrockbailbonds.biz",
            session_tenant="other_agency",
            header_tenant=None,
            is_platform_admin=False,
        )
        == SHAMROCK_TENANT_ID
    )


def test_middleware_fail_closed_only_when_flag_on(monkeypatch):
    from dashboard.tenancy.context import TenantContextMiddleware

    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)

    @app.get("/who")
    def who():
        return {"tenant": current_tenant_id()}

    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    off = TestClient(app, base_url="http://evil.example")
    assert off.get("/who").status_code == 200
    assert off.get("/who").json()["tenant"] == SHAMROCK_TENANT_ID

    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    denied = TestClient(app, base_url="http://evil.example")
    assert denied.get("/who").status_code == 403
    assert denied.get("/who").json() == {"error": "tenant_required"}

    home = TestClient(app, base_url="http://leads.shamrockbailbonds.biz")
    assert home.get("/who").json()["tenant"] == SHAMROCK_TENANT_ID

    customer = TestClient(app, base_url="http://acme.app.shamrockbailbonds.biz")
    assert customer.get("/who").json()["tenant"] == "acme"


def test_session_cookie_carries_shamrock_and_legacy_cookies_still_load(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    from dashboard.auth.pin_middleware import _attach_session, _load_session, _sign_token
    from starlette.requests import Request

    token = _sign_token(email="admin@shamrockbailbonds.biz", role="god_admin", is_admin=True)
    loaded = _load_session(token)
    assert loaded["tenant_id"] == SHAMROCK_TENANT_ID

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 5000),
    }
    request = Request(scope)
    _attach_session(request, {"auth": True, "role": "staff", "email": "clerk@example.com"})
    assert request.state.sl_tenant_id == SHAMROCK_TENANT_ID


def test_indexes_are_defined_and_not_wired_into_startup():
    specs = tenant_index_specs()
    names = {spec.name for spec in specs}
    assert "tenant_poa_number" in names
    assert "tenant_bond_case_id" in names
    assert "tenant_gcal_dedup" in names
    for spec in specs:
        assert spec.keys[0] == ("tenant_id", 1)
        assert "expireAfterSeconds" not in spec.as_create_kwargs()
    policy = audit_retention_policy()
    assert policy["current_ttl_seconds"] == AUDIT_TTL_CURRENT_SECONDS == 7776000
    assert policy["target_seconds_money_sign_poa"] == 7 * 365 * 24 * 3600
    assert policy["applied"] is False

    cron = (ROOT / "dashboard" / "cron.py").read_text(encoding="utf-8")
    main = (ROOT / "dashboard" / "main.py").read_text(encoding="utf-8")
    assert "expireAfterSeconds=7776000" in cron
    assert "tenant_index_specs" not in cron
    assert "tenant_index_specs" not in main
    assert "bind_job_tenant" in cron
    scheduler = (ROOT / "core" / "scheduler.py").read_text(encoding="utf-8")
    assert "bind_platform_job" in scheduler


class _SyncBag:
    def __init__(self, docs):
        self.docs = docs

    def count_documents(self, filt):
        return len(self._match(filt))

    def update_many(self, filt, update):
        matched = self._match(filt)
        for doc in matched:
            doc.update(update.get("$set", {}))
            for key in (update.get("$unset") or {}):
                doc.pop(key, None)
        return SimpleNamespace(modified_count=len(matched))

    def update_one(self, filt, update, upsert=False):
        self.docs.append({"upsert": upsert, "filter": filt, "update": update})
        return SimpleNamespace(modified_count=0, upserted_id="seed")

    def _match(self, filt):
        if "$or" in filt:
            return [
                doc
                for doc in self.docs
                if "tenant_id" not in doc or doc.get("tenant_id") in (None, "")
            ]
        if filt.get("tenant_id") == "shamrock" and filt.get("tenant_backfill_rev") == 1:
            return [
                doc
                for doc in self.docs
                if doc.get("tenant_id") == "shamrock" and doc.get("tenant_backfill_rev") == 1
            ]
        return []


class _FakeDb(dict):
    def list_collection_names(self):
        return list(self.keys())


def test_backfill_dry_run_and_down_path_do_not_touch_foreign_tenants(monkeypatch):
    monkeypatch.delenv("SAAS_TENANT_BACKFILL_I_UNDERSTAND", raising=False)
    from scripts.backfill_tenant_id import assert_write_allowed, main, run_against

    bonds = _SyncBag(
        [
            {"_id": 1},
            {"_id": 2, "tenant_id": "other_agency"},
            {"_id": 3, "tenant_id": "shamrock", "tenant_backfill_rev": 1},
        ]
    )
    db = _FakeDb(active_bonds=bonds, tenants=_SyncBag([]), tenant_memberships=_SyncBag([]))
    # Only active_bonds is in the owned list and present; others are skipped.
    # tenants/memberships are seeded on apply, not via the owned-collection loop.
    report = run_against(db, dry_run=True, down=False)
    assert report["collections"]["active_bonds"]["would_update"] == 1
    assert report["collections"]["active_bonds"]["updated"] == 0
    assert bonds.docs[0] == {"_id": 1}
    assert bonds.docs[1]["tenant_id"] == "other_agency"

    applied = run_against(db, dry_run=False, down=False)
    assert applied["collections"]["active_bonds"]["updated"] == 1
    assert bonds.docs[0]["tenant_id"] == "shamrock"
    assert bonds.docs[0]["tenant_backfill_rev"] == 1
    assert bonds.docs[1]["tenant_id"] == "other_agency"

    down = run_against(db, dry_run=False, down=True)
    assert down["collections"]["active_bonds"]["unset"] == 2
    assert "tenant_id" not in bonds.docs[0]
    assert bonds.docs[1]["tenant_id"] == "other_agency"

    with pytest.raises(SystemExit):
        assert_write_allowed(True)
    with pytest.raises(SystemExit):
        main(["--apply"])


def test_backfill_cli_is_offline(capsys, monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://prod.example/do-not-dial")
    from scripts import backfill_tenant_id as script

    def boom(*args, **kwargs):
        raise AssertionError("MongoClient must not be constructed")

    monkeypatch.setattr(script, "MongoClient", boom, raising=False)
    assert script.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["connected"] is False
    assert payload["tenant_id"] == "shamrock"
    assert "active_bonds" in payload["would_touch_collections"]
    assert "arrests" in payload["global_untouched"]
    assert payload["seed_tenant"]["tenant_id"] == "shamrock"
    assert payload["indexes_defined_not_applied"]
