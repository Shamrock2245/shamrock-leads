"""POA reassign audit history and manual release stamps.

Mocked inventory only. Powers are TEST- numbers. Nothing here opens Mongo
or reads SHAMROCK_MONGO_WRITE_URI.
"""
from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _get_serializer, _sign_token
from dashboard.routers.poa import poa_bp
from dashboard.services.powers_pack import assemble_powers_pack


_SESSION_EMAIL = "office@example.invalid"
_SESSION_NAME = "Office Staff"
_BODY_ACTOR = "Body Actor"
_BODY_EMAIL = "body@example.invalid"
_BODY_AGENT = "Body Agent"
_BODY_RELEASED_BY = "Body Release"


class _Col:
    def __init__(self):
        self.docs: list[dict] = []
        self.updates: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return None

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return copy.deepcopy(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        self.updates.append(copy.deepcopy(update))
        for doc in self.docs:
            if not all(doc.get(key) == value for key, value in query.items()):
                continue
            for key, value in (update.get("$set") or {}).items():
                doc[key] = copy.deepcopy(value)
            for key in (update.get("$unset") or {}):
                doc.pop(key, None)
            for key, value in (update.get("$push") or {}).items():
                current = doc.get(key, None)
                if current is None:
                    doc[key] = [copy.deepcopy(value)]
                elif isinstance(current, list):
                    current.append(copy.deepcopy(value))
                else:
                    raise AssertionError(f"$push would overwrite non-list {key}")
            return None
        return None


def _cols():
    return {"poa_inventory": _Col(), "audit_events": _Col()}


def _client(monkeypatch, cols):
    monkeypatch.setenv("SECRET_KEY", "poa-reassign-audit-test-secret")
    monkeypatch.setattr(
        "dashboard.routers.poa.get_collection",
        lambda name: cols[name],
    )
    app = FastAPI()
    app.include_router(poa_bp)
    return TestClient(app)


def _cookie(email=_SESSION_EMAIL, agent_name=_SESSION_NAME):
    token = _sign_token(email=email, role="staff", agent_name=agent_name, is_admin=False)
    return {COOKIE_NAME: token}


def _name_only_cookie(agent_name="Casey Agent"):
    token = _get_serializer().dumps({
        "auth": True,
        "t": int(time.time()),
        "role": "staff",
        "email": "",
        "agent_name": agent_name,
    })
    return {COOKIE_NAME: token}


def _prior_entry():
    return {
        "holder": "Prior Holder",
        "case": "TEST-CASE-0",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "actor": "prior@example.invalid",
    }


def _assigned(**extra):
    doc = {
        "_id": "TEST-POWER-1",
        "poa_number": "TEST-1001",
        "surety_id": "osi",
        "status": "assigned",
        "bond_case_id": "TEST-CASE-A",
        "assigned_to_agent": "Case Holder",
        "used_at": "2026-02-01T00:00:00+00:00",
        "reassigned_from": [_prior_entry()],
    }
    doc.update(extra)
    return doc


def _reassign_body(**extra):
    body = {
        "poa_number": "TEST-1001",
        "surety_id": "osi",
        "new_booking_number": "TEST-CASE-B",
        "actor": _BODY_ACTOR,
        "email": _BODY_EMAIL,
        "agent_name": _BODY_AGENT,
        "assigned_to_agent": "Body Holder",
        "released_by": _BODY_RELEASED_BY,
    }
    body.update(extra)
    return body


def _spoof_strings():
    return (_BODY_ACTOR, _BODY_EMAIL, _BODY_AGENT, _BODY_RELEASED_BY, "Body Holder")


def _assert_no_spoof(payload):
    blob = str(payload)
    for spoof in _spoof_strings():
        assert spoof not in blob


@pytest.fixture
def wired(monkeypatch):
    cols = _cols()
    client = _client(monkeypatch, cols)
    return client, cols


def test_reassign_writes_poa_reassigned_audit_row(wired):
    client, cols = wired
    cols["poa_inventory"].docs.append(_assigned())
    before = datetime.now(timezone.utc)

    response = client.post("/api/poa/reassign", json=_reassign_body(), cookies=_cookie())

    assert response.status_code == 200, response.text
    after = datetime.now(timezone.utc)
    audits = cols["audit_events"].docs
    assert len(audits) == 1
    row = audits[0]
    assert row["action"] == "poa_reassigned"
    assert row["entity_type"] == "poa"
    assert row["entity_id"] == "TEST-1001"
    assert row["poa_number"] == "TEST-1001"
    assert row["power_id"] == "TEST-POWER-1"
    assert row["from_case"] == "TEST-CASE-A"
    assert row["from_agent"] == "Case Holder"
    assert row["to_case"] == "TEST-CASE-B"
    assert row["to_agent"] == "Case Holder"
    assert row["actor"] == _SESSION_EMAIL
    stamp = row["timestamp"]
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is not None
    assert stamp.utcoffset().total_seconds() == 0
    assert before <= stamp <= after
    assert row["details"]["power_id"] == "TEST-POWER-1"
    assert row["details"]["from_case"] == "TEST-CASE-A"
    assert row["details"]["to_case"] == "TEST-CASE-B"
    _assert_no_spoof(row)


def test_reassigned_from_appends_across_two_reassigns(wired):
    client, cols = wired
    cols["poa_inventory"].docs.append(_assigned())
    staff = _cookie()

    first = client.post("/api/poa/reassign", json=_reassign_body(), cookies=staff)
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/poa/reassign",
        json=_reassign_body(new_booking_number="TEST-CASE-C"),
        cookies=staff,
    )
    assert second.status_code == 200, second.text

    stored = cols["poa_inventory"].docs[0]
    history = stored["reassigned_from"]
    assert isinstance(history, list)
    assert len(history) == 3
    assert history[0] == _prior_entry()
    assert history[1]["holder"] == "Case Holder"
    assert history[1]["case"] == "TEST-CASE-A"
    assert history[1]["actor"] == _SESSION_EMAIL
    assert isinstance(history[1]["timestamp"], datetime)
    assert history[2]["holder"] == "Case Holder"
    assert history[2]["case"] == "TEST-CASE-B"
    assert history[2]["actor"] == _SESSION_EMAIL
    assert history[2]["timestamp"] >= history[1]["timestamp"]
    assert stored["bond_case_id"] == "TEST-CASE-C"
    assert stored["assigned_to_agent"] == "Case Holder"
    pushes = [op for op in cols["poa_inventory"].updates if "$push" in op]
    assert len(pushes) == 2
    assert all("reassigned_from" not in op.get("$set", {}) for op in pushes)
    assert len(cols["audit_events"].docs) == 2
    assert cols["audit_events"].docs[0]["to_case"] == "TEST-CASE-B"
    assert cols["audit_events"].docs[1]["from_case"] == "TEST-CASE-B"
    assert cols["audit_events"].docs[1]["to_case"] == "TEST-CASE-C"


def test_reassign_actor_comes_from_the_session_not_the_body(wired):
    client, cols = wired
    cols["poa_inventory"].docs.append(_assigned())
    cols["poa_inventory"].docs.append(_assigned(
        _id="TEST-POWER-2",
        poa_number="TEST-1002",
        bond_case_id="TEST-CASE-A2",
        reassigned_from=[],
    ))

    emailed = client.post("/api/poa/reassign", json=_reassign_body(), cookies=_cookie())
    assert emailed.status_code == 200, emailed.text
    assert cols["audit_events"].docs[0]["actor"] == _SESSION_EMAIL
    assert cols["poa_inventory"].docs[0]["reassigned_from"][-1]["actor"] == _SESSION_EMAIL
    _assert_no_spoof(cols["audit_events"].docs[0])
    _assert_no_spoof(cols["poa_inventory"].docs[0]["reassigned_from"][-1])

    named = client.post(
        "/api/poa/reassign",
        json=_reassign_body(poa_number="TEST-1002", new_booking_number="TEST-CASE-D"),
        cookies=_name_only_cookie(),
    )
    assert named.status_code == 200, named.text
    row = cols["audit_events"].docs[-1]
    assert row["actor"] == "Casey Agent"
    assert row["poa_number"] == "TEST-1002"
    assert row["from_case"] == "TEST-CASE-A2"
    assert cols["poa_inventory"].docs[-1]["reassigned_from"][-1]["actor"] == "Casey Agent"
    _assert_no_spoof(row)


def test_reassign_and_release_require_a_session(wired):
    client, cols = wired
    seed = _assigned()
    cols["poa_inventory"].docs.append(copy.deepcopy(seed))

    reassign = client.post("/api/poa/reassign", json=_reassign_body())
    assert reassign.status_code == 401
    assert reassign.json()["error"] == "auth_required"
    release = client.post(
        "/api/poa/release",
        json={
            "poa_number": "TEST-1001",
            "surety_id": "osi",
            "actor": _BODY_ACTOR,
            "released_by": _BODY_RELEASED_BY,
        },
    )
    assert release.status_code == 401
    assert release.json()["error"] == "auth_required"
    assert cols["poa_inventory"].docs[0] == seed
    assert cols["poa_inventory"].updates == []
    assert cols["audit_events"].docs == []

    blank_identity = _get_serializer().dumps({
        "auth": True,
        "t": int(time.time()),
        "role": "staff",
        "email": "",
        "agent_name": "",
    })
    nameless = client.post(
        "/api/poa/reassign",
        json=_reassign_body(),
        cookies={COOKIE_NAME: blank_identity},
    )
    assert nameless.status_code == 401
    assert cols["poa_inventory"].docs[0] == seed
    assert cols["audit_events"].docs == []


def test_release_stamps_released_at_and_released_by_and_keeps_history(wired):
    client, cols = wired
    old_released = datetime(2026, 3, 1, tzinfo=timezone.utc)
    first_history = {
        "released_at": datetime(2026, 1, 15, tzinfo=timezone.utc),
        "released_by": "first@example.invalid",
        "release_reason": "bond_renewal",
    }
    cols["poa_inventory"].docs.append(_assigned(
        _id="TEST-POWER-REL",
        poa_number="TEST-2001",
        bond_case_id="TEST-CASE-REL",
        used_at="2026-04-01T00:00:00+00:00",
        voided_at="should-clear",
        void_reason="should-clear",
        released_at=old_released,
        released_by="earlier@example.invalid",
        release_reason="exonerated",
        release_history=[first_history],
    ))
    before = datetime.now(timezone.utc)

    response = client.post(
        "/api/poa/release",
        json={
            "poa_number": "TEST-2001",
            "surety_id": "osi",
            "actor": _BODY_ACTOR,
            "released_by": _BODY_RELEASED_BY,
            "email": _BODY_EMAIL,
        },
        cookies=_cookie(),
    )

    assert response.status_code == 200, response.text
    after = datetime.now(timezone.utc)
    body = response.json()
    assert body["success"] is True
    assert body["poa_number"] == "TEST-2001"
    assert body["message"] == "POA TEST-2001 released back to available"
    stored = cols["poa_inventory"].docs[0]
    assert stored["status"] == "available"
    assert stored["bond_case_id"] is None
    assert stored["used_at"] is None
    assert "voided_at" not in stored
    assert "void_reason" not in stored
    assert stored["release_reason"] == "exonerated"
    stamp = stored["released_at"]
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is not None
    assert stamp.utcoffset().total_seconds() == 0
    assert before <= stamp <= after
    assert stamp != old_released
    assert stored["released_by"] == _SESSION_EMAIL
    history = stored["release_history"]
    assert history[0] == first_history
    assert history[1]["released_at"] == old_released
    assert history[1]["released_by"] == "earlier@example.invalid"
    assert history[1]["release_reason"] == "exonerated"
    assert stored["reassigned_from"] == [_prior_entry()]
    _assert_no_spoof({"released_by": stored["released_by"], "history": history[1]})
    pushes = [op for op in cols["poa_inventory"].updates if op.get("$push")]
    assert len(pushes) == 1
    assert "release_history" in pushes[0]["$push"]
    assert "release_history" not in pushes[0].get("$set", {})
    assert cols["audit_events"].docs == []


def test_reassign_and_release_keep_existing_behavior(wired):
    client, cols = wired
    staff = _cookie()
    cols["poa_inventory"].docs.append(_assigned())

    missing = client.post(
        "/api/poa/reassign",
        json={"poa_number": "TEST-1001", "surety_id": "osi", "actor": _BODY_ACTOR},
        cookies=staff,
    )
    assert missing.status_code == 400
    assert missing.json()["error"] == "poa_number, surety_id, and new_booking_number required"
    assert cols["audit_events"].docs == []
    assert cols["poa_inventory"].updates == []

    unknown = client.post(
        "/api/poa/reassign",
        json=_reassign_body(poa_number="TEST-404"),
        cookies=staff,
    )
    assert unknown.status_code == 404
    assert "TEST-404" in unknown.json()["error"]
    assert cols["audit_events"].docs == []

    moved = client.post("/api/poa/reassign", json=_reassign_body(), cookies=staff)
    assert moved.status_code == 200, moved.text
    payload = moved.json()
    assert payload == {
        "success": True,
        "poa_number": "TEST-1001",
        "message": "POA TEST-1001 reassigned from TEST-CASE-A → TEST-CASE-B",
    }
    stored = cols["poa_inventory"].docs[0]
    assert stored["status"] == "assigned"
    assert stored["bond_case_id"] == "TEST-CASE-B"
    assert isinstance(stored["used_at"], str)
    assert stored["used_at"].endswith("+00:00")
    assert stored["assigned_to_agent"] == "Case Holder"
    assert "released_at" not in stored

    legacy = _assigned(
        _id="TEST-POWER-LEGACY",
        poa_number="TEST-1003",
        bond_case_id="TEST-CASE-LEGACY",
        reassigned_from="TEST-CASE-OLD",
    )
    cols["poa_inventory"].docs.append(legacy)
    kept = client.post(
        "/api/poa/reassign",
        json=_reassign_body(poa_number="TEST-1003", new_booking_number="TEST-CASE-NEW"),
        cookies=staff,
    )
    assert kept.status_code == 200, kept.text
    legacy_history = cols["poa_inventory"].docs[-1]["reassigned_from"]
    assert legacy_history[0]["case"] == "TEST-CASE-OLD"
    assert legacy_history[1]["case"] == "TEST-CASE-LEGACY"
    assert legacy_history[1]["actor"] == _SESSION_EMAIL

    blank = client.post(
        "/api/poa/release",
        json={"actor": _BODY_ACTOR},
        cookies=staff,
    )
    assert blank.status_code == 400
    assert blank.json()["error"] == "poa_number and surety_id required"

    missing_power = client.post(
        "/api/poa/release",
        json={"poa_number": "TEST-404", "surety_id": "osi"},
        cookies=staff,
    )
    assert missing_power.status_code == 404

    available = _assigned(
        _id="TEST-POWER-FREE",
        poa_number="TEST-3001",
        status="available",
        bond_case_id=None,
        reassigned_from=[],
    )
    cols["poa_inventory"].docs.append(available)
    updates_before = len(cols["poa_inventory"].updates)
    conflict = client.post(
        "/api/poa/release",
        json={"poa_number": "TEST-3001", "surety_id": "osi", "released_by": _BODY_RELEASED_BY},
        cookies=staff,
    )
    assert conflict.status_code == 409
    assert "not assigned" in conflict.json()["error"]
    assert cols["poa_inventory"].docs[-1]["status"] == "available"
    assert "released_at" not in cols["poa_inventory"].docs[-1]
    assert len(cols["poa_inventory"].updates) == updates_before

    released = client.post(
        "/api/poa/release",
        json={"poa_number": "TEST-1001", "surety_id": "osi"},
        cookies=staff,
    )
    assert released.status_code == 200, released.text
    assert released.json()["message"] == "POA TEST-1001 released back to available"
    final = cols["poa_inventory"].docs[0]
    assert final["status"] == "available"
    assert final["bond_case_id"] is None
    assert final["used_at"] is None
    assert isinstance(final["released_at"], datetime)
    assert final["released_by"] == _SESSION_EMAIL
    assert "release_history" not in final
    assert len(final["reassigned_from"]) == 2


class _PackCol:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, projection=None):
        return self

    async def to_list(self, limit):
        return [dict(d) for d in self.docs[:limit]]


def test_pack_reads_appended_reassign_and_release_history():
    """The transfer sheet keeps each appended move and each kept release."""
    db = {
        "poa_inventory": _PackCol([{
            "surety_id": "osi",
            "poa_number": "TEST-9001",
            "status": "assigned",
            "bond_case_id": "TEST-CASE-C",
            "reassigned_from": [
                {
                    "holder": "Prior Holder",
                    "case": "TEST-CASE-A",
                    "timestamp": "2026-09-02T12:00:00+00:00",
                    "actor": "prior@example.invalid",
                },
                {
                    "holder": "Case Holder",
                    "case": "TEST-CASE-B",
                    "timestamp": "2026-09-10T15:00:00+00:00",
                    "actor": "office@example.invalid",
                },
            ],
            "release_history": [{
                "released_at": "2026-09-04T00:00:00+00:00",
                "released_by": "first@example.invalid",
                "release_reason": "bond_renewal",
            }],
            "released_at": "2026-09-18T00:00:00+00:00",
            "released_by": "office@example.invalid",
            "release_reason": "manual",
        }]),
        "active_bonds": _PackCol([]),
        "audit_events": _PackCol([]),
    }
    pack = asyncio.run(assemble_powers_pack(
        db,
        surety_id="osi",
        pack="transfer",
        start_date="2026-09-01",
        end_date="2026-09-30",
    ))
    reassigns = [row for row in pack["transfer"]["rows"] if row["event_type"] == "reassign"]
    releases = [row for row in pack["transfer"]["rows"] if row["event_type"] == "release"]
    assert [(row["from_ref"], row["to_ref"], row["event_date"]) for row in reassigns] == [
        ("TEST-CASE-A", "TEST-CASE-B", "2026-09-02"),
        ("TEST-CASE-B", "TEST-CASE-C", "2026-09-10"),
    ]
    assert [(row["event_date"], row["reason"], row["sources"]) for row in releases] == [
        ("2026-09-04", "bond_renewal", ["poa_inventory.release_history"]),
        ("2026-09-18", "manual", ["poa_inventory.released_at"]),
    ]
    assert pack["history_complete"] is False
    assert "best-effort" in pack["transfer_banner"]
