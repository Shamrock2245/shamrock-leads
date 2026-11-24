"""Concurrent confirms of one blank ID scan claim a single role.

The portal pin store is the in-memory stand-in. The first find of each
confirm is held until both have read the blank row, then the writes run
one at a time. A read-then-write claim lets both confirms proceed. A
compare-and-swap lets one proceed and refuses the other.

Synthetic TEST- records only. No DocuSeal send and no customer message.
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi.responses import JSONResponse

from dashboard.routers.pin_portal import RemainingFieldsRequest, _claim_unbound_scan_role, _self_serve_remaining_fields
from tests._inmem_mongo import FakeCollection, FakeDB

TOKEN = "PORTAL-TEST-ROLE-CLAIM"
PHONE = "2395550100"
SCAN_ID = "SCAN-TEST-BLANK"
OTHER_SCAN = "SCAN-TEST-KEPT"
NAME = "Test Claimant"


class RacingPins(FakeCollection):
    """Hold the first read of each confirm until both have seen the blank row."""

    def __init__(self, parties: int = 2):
        super().__init__()
        self._parties = parties
        self._reads = 0
        self._both_read = asyncio.Event()
        self._lock = asyncio.Lock()

    async def find_one(self, flt=None, projection=None, sort=None, **kwargs):
        result = await super().find_one(flt, projection, sort, **kwargs)
        if self._reads < self._parties:
            self._reads += 1
            if self._reads >= self._parties:
                self._both_read.set()
            else:
                await self._both_read.wait()
        return result

    async def find_one_and_update(self, flt, update, upsert=False, **kw):
        await self._both_read.wait()
        async with self._lock:
            return await super().find_one_and_update(flt, update, upsert=upsert, **kw)

    async def update_one(self, flt, update, upsert=False, **kwargs):
        await self._both_read.wait()
        async with self._lock:
            return await super().update_one(flt, update, upsert=upsert, **kwargs)


def _doc():
    return {
        "_id": "PIN-TEST-ROLE",
        "session_token": TOKEN,
        "verified": True,
        "role": "",
        "phone": PHONE,
        "packet_id": "",
        "id_scan_records": [
            {
                "scan_id": SCAN_ID,
                "role": "",
                "success": True,
                "extracted": {"full_name": NAME, "dl_number": "TEST-DL-ROLE"},
            },
            {"scan_id": OTHER_SCAN, "role": "coindemnitor", "success": True},
        ],
        "id_ocr_snapshots": [
            {"scan_id": SCAN_ID, "role": "", "extracted": {"full_name": NAME}},
            {"scan_id": OTHER_SCAN, "role": "coindemnitor", "extracted": {"full_name": "Test Other"}},
        ],
    }


def _install(monkeypatch, pins, audits):
    def get_collection(name):
        if name == "portal_pins":
            return pins
        if name == "audit_events":
            return audits
        raise AssertionError(name)

    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", get_collection)


def _request(role: str) -> RemainingFieldsRequest:
    return RemainingFieldsRequest(
        session_token=TOKEN,
        role=role,
        scan_id=SCAN_ID,
        email="admin@shamrockbailbonds.biz",
        fields={},
    )


def _audits(audits, event_type: str):
    return [row for row in audits.docs if row.get("event_type") == event_type]


def _assert_no_pii(audits):
    blob = json.dumps(audits.docs)
    assert PHONE not in blob
    assert TOKEN not in blob
    assert NAME not in blob
    assert "TEST-DL-ROLE" not in blob


@pytest.mark.asyncio
async def test_concurrent_blank_scan_claims_one_role_and_audits_both_outcomes(monkeypatch):
    pins = RacingPins()
    pins.docs.append(_doc())
    audits = FakeCollection()
    _install(monkeypatch, pins, audits)
    accepted: list[str] = []

    async def _accept(**kwargs):
        row = next(item for item in kwargs["session"]["id_scan_records"] if item["scan_id"] == SCAN_ID)
        assert row["role"] == kwargs["role"]
        accepted.append(kwargs["role"])
        return {
            "ok": True,
            "saved": True,
            "signing_link": "https://sign.shamrockbailbonds.biz/s/TEST-ROLE",
            "role": kwargs["role"],
            "packet_id": "PKT-TEST-ROLE",
        }

    monkeypatch.setattr("dashboard.services.paperwork_self_serve.accept_confirmed_id", _accept)
    sessions = {
        "defendant": copy.deepcopy(pins.docs[0]),
        "indemnitor": copy.deepcopy(pins.docs[0]),
    }

    async def _confirm(role: str):
        return await _self_serve_remaining_fields(sessions[role], _request(role))

    defendant, indemnitor = await asyncio.wait_for(
        asyncio.gather(_confirm("defendant"), _confirm("indemnitor")),
        timeout=2,
    )
    results = {"defendant": defendant, "indemnitor": indemnitor}
    successes = {role: body for role, body in results.items() if not isinstance(body, JSONResponse)}
    failures = {role: body for role, body in results.items() if isinstance(body, JSONResponse)}
    assert list(successes) and list(failures)
    assert len(successes) == 1
    assert len(failures) == 1
    winner = next(iter(successes))
    loser = next(iter(failures))
    assert successes[winner]["role"] == winner
    assert failures[loser].status_code == 400
    assert json.loads(failures[loser].body)["error"] == "role_mismatch"
    assert accepted == [winner]
    assert sessions[loser]["id_scan_records"][0]["role"] == ""
    assert sessions[loser].get("role", "") in {"", None}

    stored = pins.docs[0]
    assert stored["role"] == winner
    assert stored["id_scan_records"][0]["role"] == winner
    assert stored["id_scan_records"][0]["extracted"]["full_name"] == NAME
    assert stored["id_scan_records"][1]["role"] == "coindemnitor"
    assert stored["id_ocr_snapshots"][0]["role"] == winner
    assert stored["id_ocr_snapshots"][1]["role"] == "coindemnitor"

    claimed = _audits(audits, "id_scan_role_claimed")
    rejected = _audits(audits, "id_scan_role_claim_rejected")
    assert len(claimed) == 1
    assert len(rejected) == 1
    claim = claimed[0]
    assert claim["old_role"] == ""
    assert claim["new_role"] == winner
    assert claim["actor"] == "portal_client"
    assert claim["reason"] == "unbound_scan_role_claimed"
    assert claim["old_state"] == {"role": "", "session_role": ""}
    assert claim["new_state"] == {"role": winner, "session_role": winner}
    assert claim["entity_id"] == SCAN_ID
    assert claim["session_id"] == "PIN-TEST-ROLE"
    refusal = rejected[0]
    assert refusal["old_role"] == winner
    assert refusal["new_role"] == winner
    assert refusal["attempted_role"] == loser
    assert refusal["actor"] == "portal_client"
    assert refusal["reason"] == "conflicting_role_claim_rejected"
    assert refusal["old_state"]["role"] == winner
    assert refusal["new_state"]["role"] == winner
    _assert_no_pii(audits)


@pytest.mark.asyncio
async def test_concurrent_same_role_claims_both_proceed_with_one_audit(monkeypatch):
    pins = RacingPins()
    pins.docs.append(_doc())
    audits = FakeCollection()
    _install(monkeypatch, pins, audits)
    accepted: list[str] = []

    async def _accept(**kwargs):
        accepted.append(kwargs["role"])
        return {"ok": True, "saved": True, "signing_link": "https://sign.shamrockbailbonds.biz/s/TEST-ROLE", "role": kwargs["role"]}

    monkeypatch.setattr("dashboard.services.paperwork_self_serve.accept_confirmed_id", _accept)
    sessions = [copy.deepcopy(pins.docs[0]), copy.deepcopy(pins.docs[0])]

    async def _confirm(session):
        return await _self_serve_remaining_fields(session, _request("defendant"))

    first, second = await asyncio.wait_for(
        asyncio.gather(_confirm(sessions[0]), _confirm(sessions[1])),
        timeout=2,
    )
    assert not isinstance(first, JSONResponse)
    assert not isinstance(second, JSONResponse)
    assert first["role"] == "defendant"
    assert second["role"] == "defendant"
    assert accepted == ["defendant", "defendant"]
    assert pins.docs[0]["role"] == "defendant"
    assert pins.docs[0]["id_scan_records"][0]["role"] == "defendant"
    assert len(_audits(audits, "id_scan_role_claimed")) == 1
    assert _audits(audits, "id_scan_role_claim_rejected") == []
    claim = _audits(audits, "id_scan_role_claimed")[0]
    assert claim["old_role"] == ""
    assert claim["new_role"] == "defendant"
    assert claim["actor"] == "portal_client"
    assert claim["reason"] == "unbound_scan_role_claimed"
    _assert_no_pii(audits)


@pytest.mark.asyncio
async def test_repeat_claim_audits_once_and_a_conflict_is_refused(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_doc())
    _install(monkeypatch, db["portal_pins"], db["audit_events"])
    session = copy.deepcopy(_doc())

    first = await _claim_unbound_scan_role(session, SCAN_ID, "defendant")
    assert first == ""
    assert session["role"] == "defendant"
    assert session["id_scan_records"][0]["role"] == "defendant"
    claimed = _audits(db["audit_events"], "id_scan_role_claimed")
    assert len(claimed) == 1
    assert claimed[0]["old_role"] == ""
    assert claimed[0]["new_role"] == "defendant"
    assert claimed[0]["actor"] == "portal_client"
    assert claimed[0]["reason"] == "unbound_scan_role_claimed"

    again = await _claim_unbound_scan_role(session, SCAN_ID, "defendant")
    assert again == ""
    assert len(_audits(db["audit_events"], "id_scan_role_claimed")) == 1

    conflict = await _claim_unbound_scan_role(copy.deepcopy(_doc()), SCAN_ID, "indemnitor")
    assert conflict == "role_mismatch"
    assert db["portal_pins"].docs[0]["role"] == "defendant"
    assert db["portal_pins"].docs[0]["id_scan_records"][0]["role"] == "defendant"
    rejected = _audits(db["audit_events"], "id_scan_role_claim_rejected")
    assert len(rejected) == 1
    assert rejected[0]["old_role"] == "defendant"
    assert rejected[0]["new_role"] == "defendant"
    assert rejected[0]["attempted_role"] == "indemnitor"
    assert rejected[0]["actor"] == "portal_client"
    assert rejected[0]["reason"] == "conflicting_role_claim_rejected"
    _assert_no_pii(db["audit_events"])


@pytest.mark.asyncio
async def test_scan_born_with_a_role_is_not_a_new_claim(monkeypatch):
    db = FakeDB()
    doc = _doc()
    doc["role"] = "indemnitor"
    doc["id_scan_records"][0]["role"] = "indemnitor"
    doc["id_ocr_snapshots"][0]["role"] = "indemnitor"
    db["portal_pins"].docs.append(doc)
    _install(monkeypatch, db["portal_pins"], db["audit_events"])
    session = copy.deepcopy(doc)

    same = await _claim_unbound_scan_role(session, SCAN_ID, "indemnitor")
    assert same == ""
    assert db["audit_events"].docs == []

    other = await _claim_unbound_scan_role(session, SCAN_ID, "defendant")
    assert other == "role_mismatch"
    assert db["portal_pins"].docs[0]["id_scan_records"][0]["role"] == "indemnitor"
    rejected = _audits(db["audit_events"], "id_scan_role_claim_rejected")
    assert len(rejected) == 1
    assert rejected[0]["old_role"] == "indemnitor"
    assert rejected[0]["attempted_role"] == "defendant"
    assert _audits(db["audit_events"], "id_scan_role_claimed") == []

    missing = await _claim_unbound_scan_role(session, "SCAN-TEST-ABSENT", "indemnitor")
    assert missing == "scan_required"
    assert len(db["audit_events"].docs) == 1


@pytest.mark.asyncio
async def test_missing_role_fields_still_claim_once(monkeypatch):
    db = FakeDB()
    doc = _doc()
    doc.pop("role")
    doc["id_scan_records"][0].pop("role")
    doc["id_ocr_snapshots"][0].pop("role")
    db["portal_pins"].docs.append(doc)
    _install(monkeypatch, db["portal_pins"], db["audit_events"])

    claimed = await _claim_unbound_scan_role(copy.deepcopy(doc), SCAN_ID, "defendant")
    assert claimed == ""
    stored = db["portal_pins"].docs[0]
    assert stored["role"] == "defendant"
    assert stored["id_scan_records"][0]["role"] == "defendant"
    assert stored["id_ocr_snapshots"][0]["role"] == "defendant"
    assert stored["id_scan_records"][1]["role"] == "coindemnitor"
    row = _audits(db["audit_events"], "id_scan_role_claimed")[0]
    assert row["old_role"] == ""
    assert row["new_role"] == "defendant"
    assert row["old_state"]["session_role"] == ""
    assert row["new_state"]["session_role"] == "defendant"

    refused = await _claim_unbound_scan_role(copy.deepcopy(doc), SCAN_ID, "indemnitor")
    assert refused == "role_mismatch"
    assert stored["role"] == "defendant"
    assert len(_audits(db["audit_events"], "id_scan_role_claimed")) == 1


class _FailOnceAudits(FakeCollection):
    def __init__(self):
        super().__init__()
        self.remaining = 1

    async def insert_one(self, doc):
        if self.remaining:
            self.remaining -= 1
            raise RuntimeError("audit_write_failed")
        await super().insert_one(doc)


@pytest.mark.asyncio
async def test_same_role_retry_writes_the_claim_audit_after_insert_failure(monkeypatch):
    pins = FakeCollection()
    pins.docs.append(_doc())
    audits = _FailOnceAudits()
    _install(monkeypatch, pins, audits)
    accepted: list[str] = []

    async def _accept(**kwargs):
        row = next(item for item in kwargs["session"]["id_scan_records"] if item["scan_id"] == SCAN_ID)
        assert row["role"] == kwargs["role"]
        accepted.append(kwargs["role"])
        return {
            "ok": True,
            "saved": True,
            "signing_link": "https://sign.shamrockbailbonds.biz/s/TEST-ROLE",
            "role": kwargs["role"],
        }

    monkeypatch.setattr("dashboard.services.paperwork_self_serve.accept_confirmed_id", _accept)

    with pytest.raises(RuntimeError, match="audit_write_failed"):
        await _claim_unbound_scan_role(copy.deepcopy(_doc()), SCAN_ID, "defendant")

    stored = pins.docs[0]
    assert stored["role"] == "defendant"
    assert stored["id_scan_records"][0]["role"] == "defendant"
    assert stored["id_scan_records"][0]["role_claimed_prior_session_role"] == ""
    assert stored["id_scan_records"][0]["role_claimed_at"]
    assert "role_claimed_at" not in stored["id_scan_records"][1]
    assert audits.docs == []

    repaired = await _self_serve_remaining_fields(copy.deepcopy(_doc()), _request("defendant"))
    assert not isinstance(repaired, JSONResponse)
    assert repaired["role"] == "defendant"
    assert accepted == ["defendant"]
    claimed = _audits(audits, "id_scan_role_claimed")
    assert len(claimed) == 1
    row = claimed[0]
    assert row["old_role"] == ""
    assert row["new_role"] == "defendant"
    assert row["actor"] == "portal_client"
    assert row["reason"] == "unbound_scan_role_claimed"
    assert row["old_state"] == {"role": "", "session_role": ""}
    assert row["new_state"] == {"role": "defendant", "session_role": "defendant"}
    assert row["created_at"] == stored["id_scan_records"][0]["role_claimed_at"]
    assert row["event_id"] == row["_id"]

    again = await _claim_unbound_scan_role(copy.deepcopy(_doc()), SCAN_ID, "defendant")
    assert again == ""
    assert len(_audits(audits, "id_scan_role_claimed")) == 1
    assert audits.docs[0]["event_id"] == row["event_id"]
    _assert_no_pii(audits)


@pytest.mark.asyncio
async def test_claim_records_an_existing_session_role_as_the_prior_role(monkeypatch):
    db = FakeDB()
    doc = _doc()
    doc["role"] = "defendant"
    db["portal_pins"].docs.append(doc)
    _install(monkeypatch, db["portal_pins"], db["audit_events"])

    claimed = await _claim_unbound_scan_role(copy.deepcopy(doc), SCAN_ID, "defendant")
    assert claimed == ""
    stored = db["portal_pins"].docs[0]
    assert stored["role"] == "defendant"
    assert stored["id_scan_records"][0]["role_claimed_prior_session_role"] == "defendant"
    row = _audits(db["audit_events"], "id_scan_role_claimed")[0]
    assert row["old_state"] == {"role": "", "session_role": "defendant"}
    assert row["new_state"] == {"role": "defendant", "session_role": "defendant"}
