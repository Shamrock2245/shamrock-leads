"""Paperwork handoff token: verify, consume, and fall back to the PIN.

No live DocuSeal, SwipeSimple, BlueBubbles, or email. TEST- ids only.
"""
from __future__ import annotations

import asyncio
import copy
import re
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.main import app
from dashboard.services.paperwork_handoff import (
    consume_handoff,
    encode_handoff_token,
    sign_handoff_token,
)

SECRET = "test-handoff-secret-not-a-credential"
CASE = "TEST-CASE-HT"
PERSON = "TEST-PERSON-HT"
NAME = "TEST INDEMNITOR"


def _ensure_route():
    paths = {getattr(route, "path", "") for route in app.routes}
    if "/api/portal/handoff/consume" not in paths:
        from dashboard.routers.pin_portal import pin_portal_router

        app.include_router(pin_portal_router)


_ensure_route()
client = TestClient(app)


def _match_field(doc, key, cond):
    present = key in doc
    value = doc.get(key)
    if isinstance(cond, dict) and cond and all(str(op).startswith("$") for op in cond):
        for op, arg in cond.items():
            if op == "$exists":
                if bool(arg) != present:
                    return False
            elif op == "$ne":
                if value == arg:
                    return False
            elif op == "$nin":
                if value in arg:
                    return False
            elif op == "$regex":
                if not isinstance(value, str) or re.search(arg, value) is None:
                    return False
            else:
                raise AssertionError(op)
        return True
    return present and value == cond


def _matches(doc, query):
    if not query:
        return True
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, item) for item in cond):
                return False
        elif key == "$and":
            if not all(_matches(doc, item) for item in cond):
                return False
        elif not _match_field(doc, key, cond):
            return False
    return True


def _apply(doc, update, *, insert):
    for op, fields in (update or {}).items():
        if op == "$setOnInsert":
            if not insert:
                continue
            for key, value in fields.items():
                doc[key] = copy.deepcopy(value)
        elif op == "$set":
            for key, value in fields.items():
                doc[key] = copy.deepcopy(value)
        elif op == "$push":
            for key, value in fields.items():
                current = doc.get(key)
                current = list(current) if isinstance(current, list) else []
                current.append(copy.deepcopy(value))
                doc[key] = current
        else:
            raise AssertionError(op)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        docs = list(self._docs)
        if length is None:
            return docs
        return docs[:length]


class MemoryCollection:
    def __init__(self):
        self.docs = []
        self._lock = asyncio.Lock()

    async def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        self.docs.append(stored)
        return stored

    async def find_one(self, query=None, *args, **kwargs):
        for doc in self.docs:
            if _matches(doc, query or {}):
                return copy.deepcopy(doc)
        return None

    def find(self, query=None, *args, **kwargs):
        return _Cursor([copy.deepcopy(doc) for doc in self.docs if _matches(doc, query or {})])

    async def update_one(self, query, update, upsert=False, **kwargs):
        for doc in self.docs:
            if _matches(doc, query or {}):
                _apply(doc, update, insert=False)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            created = {}
            _apply(created, update, insert=True)
            self.docs.append(created)
            return MagicMock(matched_count=0, modified_count=0)
        return MagicMock(matched_count=0, modified_count=0)

    async def find_one_and_update(self, query, update, upsert=False, return_document=False, **kwargs):
        async with self._lock:
            for doc in self.docs:
                if _matches(doc, query or {}):
                    before = copy.deepcopy(doc)
                    _apply(doc, update, insert=False)
                    return copy.deepcopy(doc) if return_document else before
            if not upsert:
                return None
            created = {}
            for key, value in (query or {}).items():
                if not str(key).startswith("$") and not isinstance(value, dict):
                    created[key] = copy.deepcopy(value)
            _apply(created, update, insert=True)
            self.docs.append(created)
            return copy.deepcopy(created) if return_document else None


class MemoryDB:
    def __init__(self):
        self._cols = {}

    def get(self, name):
        if name not in self._cols:
            self._cols[name] = MemoryCollection()
        return self._cols[name]

    def rows(self, name):
        return self.get(name).docs


@contextmanager
def _db_patch(db):
    with patch("dashboard.services.paperwork_handoff.get_collection", side_effect=db.get), \
         patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get):
        yield


def _enable(monkeypatch, *, flag="1", secret=SECRET):
    if flag is None:
        monkeypatch.delenv("PAPERWORK_HANDOFF_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PAPERWORK_HANDOFF_ENABLED", flag)
    if secret is None:
        monkeypatch.delenv("PAPERWORK_HANDOFF_SECRET", raising=False)
    else:
        monkeypatch.setenv("PAPERWORK_HANDOFF_SECRET", secret)


def _person(role, person_id, **extra):
    doc = {"status": "active", "full_name": NAME, **extra}
    if role == "defendant":
        doc["defendant_id"] = person_id
    else:
        doc["indemnitor_id"] = person_id
    return doc


def _bond(case_id, role, person_id, poa="POA-TEST-1"):
    doc = {
        "bond_case_id": case_id,
        "poa_number": poa,
        "status": "active",
    }
    if role == "defendant":
        doc["defendant_id"] = person_id
    elif role == "coindemnitor":
        doc["co_indemnitor_id"] = person_id
        doc["indemnitor_id"] = "TEST-PERSON-PRIMARY"
    else:
        doc["indemnitor_id"] = person_id
    return doc


def _packet(case_id, role, *, scan_url=True, submitter_status="pending", packet_status="pending_signature"):
    submitters = []
    if role == "coindemnitor":
        submitters.append({
            "id": 6,
            "role": "indemnitor",
            "status": "pending",
            "sign_url": "https://sign.shamrockbailbonds.biz/s/test-primary",
        })
    submitters.append({
        "id": 8 if role == "coindemnitor" else 7,
        "role": role,
        "status": submitter_status,
        "sign_url": f"https://sign.shamrockbailbonds.biz/s/test-{role}" if scan_url else "",
    })
    return {
        "packet_id": "PKT-TEST-HT",
        "bond_case_id": case_id,
        "status": packet_status,
        "voided": False,
        "poa_number": "POA-TEST-1",
        "created_at": "2026-11-04T00:00:00+00:00",
        "client_fields": {"indemnitor_name": "TEST PRIMARY"},
        "docuseal_submitters": submitters,
        "cosigner_verified_token": "keep-me",
    }


def _world(role="indemnitor", *, person_id=PERSON, case_id=CASE, person_extra=None, submitter_status="pending"):
    db = MemoryDB()
    extra = {"id_scan": {"success": True, "full_name": NAME}}
    if person_extra:
        extra.update(person_extra)
    collection = "defendants" if role == "defendant" else "indemnitors"
    db.get(collection).docs.append(_person(role, person_id, **extra))
    db.get("bond_cases").docs.append(_bond(case_id, role, person_id))
    db.get("paperwork_packets").docs.append(_packet(case_id, role, submitter_status=submitter_status))
    db.get("cosigner_links").docs.append({
        "token": "cosigner-test-token",
        "packet_id": "PKT-TEST-HT",
        "bond_case_id": case_id,
    })
    return db


def _token(role="indemnitor", mode="", *, person_id=PERSON, case_id=CASE, now=None, ttl=900):
    return sign_handoff_token(
        case_id=case_id,
        role=role,
        person_id=person_id,
        mode=mode,
        now=int(time.time()) if now is None else now,
        ttl_seconds=ttl,
    )


def _consume(db, token, *, role="", case="", mode=""):
    with _db_patch(db):
        response = client.post("/api/portal/handoff/consume", json={
            "ht": token,
            "role": role,
            "case": case,
            "mode": mode,
        })
    return response


def _assert_pin_fallback(response):
    assert response.status_code == 200
    assert response.json() == {"ok": False, "fallback": "pin"}


def test_mint_route_is_not_in_this_app():
    from dashboard.routers.pin_portal import pin_portal_router

    paths = {getattr(route, "path", "") for route in pin_portal_router.routes}
    assert "/api/portal/handoff-token" not in paths
    assert "/api/portal/handoff/consume" in paths


def test_flag_off_ignores_a_valid_token(monkeypatch):
    _enable(monkeypatch, flag=None)
    db = _world()
    token = _token()
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []
    assert db.rows("portal_pins") == []


def test_secret_missing_falls_back(monkeypatch):
    db = _world()
    _enable(monkeypatch)
    token = _token()
    monkeypatch.delenv("PAPERWORK_HANDOFF_SECRET", raising=False)
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []


def test_bad_signature_leaks_nothing(monkeypatch, caplog):
    _enable(monkeypatch)
    db = _world()
    token = _token()
    # Flip a payload character. The final base64 digit can be unused padding.
    pivot = 10
    broken = token[:pivot] + ("A" if token[pivot] != "A" else "B") + token[pivot + 1:]
    with caplog.at_level("INFO"):
        response = _consume(db, broken, role="indemnitor", case=CASE)
    _assert_pin_fallback(response)
    assert db.rows("paperwork_handoff_jti") == []
    blob = response.text + caplog.text + str(db.rows("audit_events"))
    assert NAME not in blob
    assert broken not in blob
    assert CASE not in response.text


def test_expired_token_falls_back(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    token = _token(now=int(time.time()) - 1000)
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []


def test_lifetime_over_15_minutes_is_rejected(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    issued = int(time.time())
    token = encode_handoff_token({
        "aud": "paperwork",
        "case_id": CASE,
        "role": "indemnitor",
        "person_id": PERSON,
        "mode": "",
        "iat": issued,
        "exp": issued + 901,
        "jti": "jti-too-long",
    })
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []


def test_wrong_audience_is_rejected(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    issued = int(time.time())
    token = encode_handoff_token({
        "aud": "leads",
        "case_id": CASE,
        "role": "indemnitor",
        "person_id": PERSON,
        "mode": "",
        "iat": issued,
        "exp": issued + 900,
        "jti": "jti-audience",
    })
    _assert_pin_fallback(_consume(db, token))
    assert db.rows("paperwork_handoff_jti") == []


def test_voided_signer_falls_back(monkeypatch):
    _enable(monkeypatch)
    db = _world(person_extra={"status": "voided", "id_scan": {"success": True, "full_name": NAME}})
    _assert_pin_fallback(_consume(db, _token(), role="indemnitor", case=CASE))
    assert db.rows("portal_pins") == []
    assert db.rows("paperwork_handoff_jti") == []


def test_unverified_identity_falls_back(monkeypatch):
    _enable(monkeypatch)
    db = _world(person_extra={"identity_verified": False})
    _assert_pin_fallback(_consume(db, _token(), role="indemnitor", case=CASE))
    assert db.rows("portal_pins") == []


def test_unverified_submitter_falls_back(monkeypatch):
    _enable(monkeypatch)
    db = _world(submitter_status="unverified")
    _assert_pin_fallback(_consume(db, _token(), role="indemnitor", case=CASE))
    assert db.rows("portal_pins") == []
    assert "sign.shamrockbailbonds.biz" not in _consume(db, _token(), role="indemnitor", case=CASE).text


def test_person_mismatch_falls_back(monkeypatch):
    _enable(monkeypatch)
    db = _world(person_id="TEST-PERSON-OTHER")
    token = _token(person_id=PERSON)
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []


def test_query_role_mismatch_does_not_consume(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    token = _token()
    _assert_pin_fallback(_consume(db, token, role="defendant", case=CASE))
    assert db.rows("paperwork_handoff_jti") == []
    ok = _consume(db, token, role="indemnitor", case=CASE)
    body = ok.json()
    assert body["ok"] is True
    assert body["role"] == "indemnitor"


def test_query_case_mismatch_does_not_consume(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    token = _token()
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case="TEST-CASE-OTHER"))
    assert db.rows("paperwork_handoff_jti") == []
    assert _consume(db, token, role="indemnitor", case=CASE).json()["ok"] is True


def test_mode_hint_does_not_override_or_reject(monkeypatch):
    _enable(monkeypatch)
    db = _world(role="coindemnitor")
    token = _token(role="coindemnitor", mode="add-cosigner")
    body = _consume(db, token, role="co-indemnitor", case=CASE, mode="id-upload").json()
    assert body["ok"] is True
    assert body["mode"] == "add-cosigner"
    assert body["route"] == "add-cosigner"
    assert body["role"] == "coindemnitor"


@pytest.mark.parametrize("role", ["indemnitor", "coindemnitor", "defendant"])
def test_happy_path_per_role(monkeypatch, role, caplog):
    _enable(monkeypatch)
    db = _world(role=role)
    token = _token(role=role)
    with caplog.at_level("INFO"):
        body = _consume(db, token, role=role, case=CASE).json()
    assert body["ok"] is True
    assert body["role"] == role
    assert body["case_id"] == CASE
    assert body["route"] == "case"
    assert body["signing_link"] == f"https://sign.shamrockbailbonds.biz/s/test-{role}"
    assert "payment_link" not in body
    assert "pay_url" not in body
    assert NAME not in str(body)
    assert token not in str(body)
    assert NAME not in caplog.text
    assert token not in caplog.text
    session = db.rows("portal_pins")[0]
    assert session["auth_method"] == "handoff"
    assert session["verified"] is True
    assert session["role"] == role
    assert session["bond_case_id"] == CASE
    assert "phone" not in session
    assert db.rows("bond_cases")[0]["poa_number"] == "POA-TEST-1"


def test_replay_is_rejected(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    token = _token()
    assert _consume(db, token, role="indemnitor", case=CASE).json()["ok"] is True
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    assert len(db.rows("paperwork_handoff_jti")) == 1
    assert len([row for row in db.rows("portal_pins") if row.get("auth_method") == "handoff"]) == 1


def test_no_id_scan_means_no_docuseal_url(monkeypatch):
    _enable(monkeypatch)
    db = _world(person_extra={"id_scan": None})
    # id_scan: None still counts as the key existing. Remove it.
    db.rows("indemnitors")[0].pop("id_scan", None)
    body = _consume(db, _token(), role="indemnitor", case=CASE).json()
    assert body["ok"] is True
    assert body["signing_link"] == ""
    assert "sign.shamrockbailbonds.biz" not in str(body)


def test_poa_change_does_not_invalidate_the_token(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    db.rows("bond_cases")[0]["poa_number"] = "POA-TEST-2"
    db.rows("paperwork_packets")[0]["poa_number"] = "POA-TEST-2"
    token = _token()
    assert "POA" not in token
    body = _consume(db, token, role="indemnitor", case=CASE).json()
    assert body["ok"] is True
    assert body["signing_link"].endswith("/s/test-indemnitor")
    assert db.rows("paperwork_packets")[0]["poa_number"] == "POA-TEST-2"


def test_add_cosigner_does_not_touch_links_or_the_packet(monkeypatch):
    _enable(monkeypatch)
    db = _world(role="coindemnitor", person_extra={"id_scan": None})
    db.rows("indemnitors")[0].pop("id_scan", None)
    before_packet = copy.deepcopy(db.rows("paperwork_packets")[0])
    before_link = copy.deepcopy(db.rows("cosigner_links")[0])
    token = _token(role="coindemnitor", mode="add-cosigner")
    docuseal = MagicMock()
    docuseal.update_submitter = AsyncMock()
    with _db_patch(db), patch("dashboard.services.docuseal_service.DocuSealService", return_value=docuseal):
        opened = client.post("/api/portal/handoff/consume", json={
            "ht": token, "role": "coindemnitor", "case": CASE, "mode": "add-cosigner",
        }).json()
        assert opened["route"] == "add-cosigner"
        assert opened["signing_link"] == ""
        saved = client.post("/api/portal/remaining-fields", json={
            "session_token": opened["session_token"],
            "role": "indemnitor",
            "staff_review_acknowledged": True,
            "fields": {
                "indemnitor_name": "TEST SHOULD NOT STICK",
                "coindemnitor_name": "TEST CO SIGNER",
                "poa_number": "POA-TEST-NO",
            },
        })
    assert saved.status_code == 200
    body = saved.json()
    assert body["signing_link"] == ""
    assert body["pushed_to_docuseal"] is False
    docuseal.update_submitter.assert_not_called()
    packet = db.rows("paperwork_packets")[0]
    assert packet["status"] == before_packet["status"]
    assert packet["poa_number"] == before_packet["poa_number"]
    assert packet["voided"] == before_packet["voided"]
    assert packet["client_fields"] == before_packet["client_fields"]
    assert packet["docuseal_submitters"] == before_packet["docuseal_submitters"]
    assert packet["cosigner_verified_token"] == before_packet["cosigner_verified_token"]
    assert packet["coindemnitor_fields"]["coindemnitor_name"] == "TEST CO SIGNER"
    assert "indemnitor_name" not in packet["coindemnitor_fields"]
    assert db.rows("cosigner_links")[0] == before_link


def test_add_cosigner_with_a_scan_updates_only_that_submitter(monkeypatch):
    _enable(monkeypatch)
    db = _world(role="coindemnitor")
    token = _token(role="coindemnitor", mode="add-cosigner")
    docuseal = MagicMock()
    docuseal.update_submitter = AsyncMock(return_value={"id": 8})
    with _db_patch(db), patch("dashboard.services.docuseal_service.DocuSealService", return_value=docuseal):
        opened = client.post("/api/portal/handoff/consume", json={
            "ht": token, "role": "coindemnitor", "case": CASE,
        }).json()
        saved = client.post("/api/portal/remaining-fields", json={
            "session_token": opened["session_token"],
            "staff_review_acknowledged": True,
            "fields": {"coindemnitor_name": "TEST CO SIGNER"},
        })
    assert saved.status_code == 200
    assert saved.json()["signing_link"].endswith("/s/test-coindemnitor")
    assert saved.json()["pushed_to_docuseal"] is True
    docuseal.update_submitter.assert_awaited()
    assert docuseal.update_submitter.await_args.args[0] == 8
    flags = docuseal.update_submitter.await_args.kwargs
    assert flags["send_email"] is False
    assert flags["send_sms"] is False
    assert db.rows("paperwork_packets")[0]["status"] == "pending_signature"
    assert db.rows("cosigner_links")[0]["token"] == "cosigner-test-token"


def test_id_upload_delegates_when_the_handler_exists(monkeypatch):
    _enable(monkeypatch)
    db = _world(person_extra={"id_scan": None})
    db.rows("indemnitors")[0].pop("id_scan", None)
    calls = []

    def handle_id_upload_mode(payload):
        calls.append(payload)
        return {"signing_link": "https://sign.shamrockbailbonds.biz/s/should-not-pass"}

    import sys
    import types
    module = types.ModuleType("dashboard.services.paperwork_id_upload")
    module.handle_id_upload_mode = handle_id_upload_mode
    sys.modules["dashboard.services.paperwork_id_upload"] = module
    try:
        body = _consume(db, _token(mode="id-upload"), role="indemnitor", case=CASE).json()
    finally:
        sys.modules.pop("dashboard.services.paperwork_id_upload", None)
    assert body["ok"] is True
    assert body["route"] == "id-upload"
    assert body["id_upload_delegated"] is True
    assert body["signing_link"] == ""
    assert calls and calls[0]["case_id"] == CASE and calls[0]["mode"] == "id-upload"


def test_id_upload_without_a_handler_stays_on_the_normal_flow(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    import sys
    sys.modules.pop("dashboard.services.paperwork_id_upload", None)
    body = _consume(db, _token(mode="id-upload"), role="indemnitor", case=CASE).json()
    assert body["ok"] is True
    assert body["route"] == "id-upload"
    assert body["id_upload_delegated"] is False
    assert body["signing_link"].endswith("/s/test-indemnitor")


def test_ocr_runs_only_after_the_token_is_consumed(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    token = _token()
    scanned = AsyncMock(return_value={"success": True, "extracted": {"full_name": "TEST SCAN", "success": True}})
    with _db_patch(db), patch("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", scanned):
        rejected = client.post("/api/portal/id-ocr", data={"session_token": token})
        assert rejected.status_code == 401
        assert rejected.json()["success"] is False
        assert "sign.shamrockbailbonds.biz" not in rejected.text
        assert scanned.await_count == 0
        opened = client.post("/api/portal/handoff/consume", json={
            "ht": token, "role": "indemnitor", "case": CASE,
        }).json()
        assert scanned.await_count == 0
        response = client.post(
            "/api/portal/id-ocr",
            data={"session_token": opened["session_token"]},
            files={"file": ("id.jpg", b"\xff\xd8\xff", "image/jpeg")},
        )
    assert response.status_code == 200
    assert scanned.await_count == 1
    assert response.json()["pushed_to_docuseal"] is False


def test_signer_scan_is_insert_only(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    original = {"success": True, "marker": "original", "full_name": NAME}
    db.rows("indemnitors")[0]["id_scan"] = original
    db.rows("indemnitors")[0]["id_scan_history"] = [{"marker": "kept"}]
    token = _token()
    scanned = AsyncMock(return_value={
        "success": True,
        "extracted": {"full_name": "TEST SCAN", "success": True, "dl_number": "SHOULD-NOT-BE-STORED"},
    })
    with _db_patch(db), patch("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", scanned):
        opened = client.post("/api/portal/handoff/consume", json={
            "ht": token, "role": "indemnitor", "case": CASE,
        }).json()
        for _ in range(2):
            client.post(
                "/api/portal/id-ocr",
                data={"session_token": opened["session_token"]},
                files={"file": ("id.jpg", b"\xff\xd8\xff", "image/jpeg")},
            )
    person = db.rows("indemnitors")[0]
    assert person["id_scan"]["marker"] == "original"
    assert person["id_scan"]["full_name"] == NAME
    history = person["id_scan_history"]
    assert history[0] == {"marker": "kept"}
    assert len(history) == 3
    assert all("dl_number" not in row for row in history[1:])
    assert all("full_name" not in row for row in history[1:])


def test_audit_history_is_not_deleted(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    _assert_pin_fallback(_consume(db, "not-a-token"))
    token = _token()
    assert _consume(db, token, role="indemnitor", case=CASE).json()["ok"] is True
    _assert_pin_fallback(_consume(db, token, role="indemnitor", case=CASE))
    actions = [row["action"] for row in db.rows("audit_events")]
    assert actions[0] == "handoff_rejected"
    assert "handoff_consumed" in actions
    assert actions.count("handoff_rejected") >= 2
    blob = str(db.rows("audit_events"))
    assert NAME not in blob
    assert "v1." not in blob


def test_pin_fallback_still_verifies(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    _assert_pin_fallback(_consume(db, "not-a-token", role="defendant", case="TEST-CASE-OTHER"))
    db.get("portal_pins").docs.append({
        "phone": "2395550199",
        "pin": "123456",
        "verified": False,
        "role": "indemnitor",
        "booking_number": "",
        "intake_id": "",
        "expires_at": "2099-01-01T00:00:00+00:00",
    })
    with _db_patch(db):
        response = client.post("/api/portal/verify-pin", json={"phone": "2395550199", "pin": "123456"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["verified"] is True
    assert str(body["session_token"]).startswith("PORTAL-")
    assert not str(body["session_token"]).startswith("PORTAL-HT-")


def test_query_params_without_a_token_do_not_grant_access():
    response = client.get(
        "/api/portal/portal-ui",
        params={"role": "defendant", "case": "TEST-CASE-SECRET", "mode": "add-cosigner"},
    )
    assert response.status_code == 200
    text = response.text
    assert "TEST-CASE-SECRET" not in text
    assert "Send Access PIN" in text
    assert "/api/portal/handoff/consume" in text
    assert "SLPaperworkIdUpload" in text
    assert "Add a co-indemnitor" in text
    assert "do not set portalMem.role" in text


def test_concurrent_consume_is_single_use(monkeypatch):
    _enable(monkeypatch)
    db = _world()
    issued = int(time.time())
    token = _token(now=issued)

    async def both():
        with _db_patch(db):
            return await asyncio.gather(
                consume_handoff(token=token, hint_role="indemnitor", hint_case=CASE, now=issued),
                consume_handoff(token=token, hint_role="indemnitor", hint_case=CASE, now=issued),
            )

    results = asyncio.run(both())
    assert sorted(item["ok"] for item in results) == [False, True]
    assert len(db.rows("paperwork_handoff_jti")) == 1
    assert len([row for row in db.rows("portal_pins") if row.get("auth_method") == "handoff"]) == 1
