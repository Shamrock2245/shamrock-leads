"""Florida federal bond-risk alerts. No live HTTP and no real send credentials."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.services.courtlistener_client import (
    DOCKET_BRIEF_FIELDS,
    DOCKET_ENTRY_FIELDS,
    DOCKET_PARTY_FIELDS,
    CourtListenerClient,
)
from dashboard.services.federal_bond_risk import (
    FLAG,
    MAX_ENTRY_PAGES,
    MAX_SEARCH_PAGES,
    ClientIdentity,
    DocketEvent,
    DocketParty,
    DocketView,
    classify_bond_risk,
    full_name_equal,
    load_active_clients,
    match_client,
    run_federal_bond_risk_scan,
)

ROOT = Path(__file__).resolve().parents[1]
_SEND_ENV = (
    "SLACK_WEBHOOK_ALERTS",
    "SLACK_WEBHOOK_LEADS",
    "SLACK_WEBHOOK_URL",
    "SLACK_WEBHOOK_ERRORS",
    "SLACK_WEBHOOK_ARRESTS",
    "SLACK_WEBHOOK_INTAKE",
    "SLACK_WEBHOOK_REARREST",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
    "BLUEBUBBLES_URL",
    "BLUEBUBBLES_PASSWORD",
    "BLUEBUBBLES_URL_0178",
    "BLUEBUBBLES_PASSWORD_0178",
    "COURTLISTENER_API_TOKEN",
    FLAG,
)


@pytest.fixture(autouse=True)
def _clear_live_sends(monkeypatch):
    for name in _SEND_ENV:
        monkeypatch.delenv(name, raising=False)


def _stored(doc: dict, key: str):
    if key in doc or "." not in key:
        return doc.get(key), key in doc
    current = doc
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _match(doc: dict, filt: dict | None) -> bool:
    if not filt:
        return True
    for key, expected in filt.items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        if key == "$and":
            if not all(_match(doc, sub) for sub in expected):
                return False
            continue
        value, present = _stored(doc, key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
            continue
        if isinstance(expected, dict) and "$gt" in expected:
            if value is None or not value > expected["$gt"]:
                return False
            continue
        if isinstance(expected, dict) and "$ne" in expected:
            if value == expected["$ne"]:
                return False
            continue
        if isinstance(expected, dict) and "$lt" in expected:
            if value is None or not str(value) < str(expected["$lt"]):
                return False
            continue
        if isinstance(expected, dict) and "$exists" in expected:
            if bool(expected["$exists"]) != (present and value is not None):
                return False
            continue
        if value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def limit(self, count: int):
        self.docs = self.docs[:count]
        return self

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda row: row.get(key) or "", reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        rows = self.docs if length is None else self.docs[:length]
        return [dict(row) for row in rows]


class _WriteResult:
    def __init__(self, matched_count: int, upserted_id):
        self.matched_count = matched_count
        self.upserted_id = upserted_id
        self.modified_count = matched_count


class _Collection:
    def find(self, filt=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, filt)])

    async def find_one(self, filt=None, projection=None, sort=None):
        for doc in self.docs:
            if _match(doc, filt):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        stored = dict(doc)
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return stored

    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), kwargs))
        return kwargs.get("name")

    async def update_one(self, filt, update, upsert=False):
        fields = update.get("$set") or {}
        if "_id" in fields:
            raise RuntimeError("immutable _id")
        pushed = update.get("$push") or {}
        added = update.get("$addToSet") or {}
        for doc in self.docs:
            if _match(doc, filt):
                doc.update(fields)
                for key, value in pushed.items():
                    current = doc.get(key)
                    if not isinstance(current, list):
                        current = []
                    current.append(value)
                    doc[key] = current
                for key, value in added.items():
                    current = doc.get(key)
                    if not isinstance(current, list):
                        current = []
                    if value not in current:
                        current.append(value)
                    doc[key] = current
                pulled = update.get("$pull") or {}
                for key, value in pulled.items():
                    current = doc.get(key)
                    if not isinstance(current, list):
                        continue
                    if isinstance(value, dict):
                        doc[key] = [
                            item for item in current
                            if not (
                                isinstance(item, dict)
                                and all(item.get(field) == expected for field, expected in value.items())
                            )
                        ]
                    else:
                        doc[key] = [item for item in current if item != value]
                return _WriteResult(matched_count=1, upserted_id=None)
        if not upsert:
            return _WriteResult(matched_count=0, upserted_id=None)
        stored = dict(filt)
        stored.update(update.get("$setOnInsert") or {})
        stored.update(update.get("$set") or {})
        stored.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _WriteResult(matched_count=0, upserted_id=stored["_id"])

    async def delete_one(self, filt):
        for idx, doc in enumerate(self.docs):
            if _match(doc, filt):
                self.docs.pop(idx)
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()


class _DB:
    def __init__(self):
        self.active_bonds = _Collection()
        self.defendants = _Collection()
        self.arrests = _Collection()
        self.docket_events = _Collection()
        self.notifications = _Collection()
        self.audit_events = _Collection()
        self.federal_bond_risk_state = _Collection()

    def __getitem__(self, name: str):
        return getattr(self, name)


class _Notifier:
    def __init__(self):
        self.calls = []

    def notify_alert(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _CourtListener:
    def __init__(self, hits=None, brief=None, parties=None, entries=None, search_result=None, rd_hits=None, search_errors=None, parties_error=None, parties_error_ids=None):
        self.hits = hits or []
        self.brief = brief or {}
        self.parties = parties or []
        self.entries = entries or []
        self.search_result = search_result
        self.rd_hits = rd_hits
        self.search_errors = search_errors or {}
        self.parties_error = parties_error
        self.parties_error_ids = parties_error_ids
        self.entries_error = None
        self.entries_error_ids = None
        self.parties_truncated = False
        self.entries_truncated = False
        self.calls = []

    async def search_limited(self, **kwargs):
        self.calls.append(("search", kwargs))
        assert kwargs["page_size"] <= 20
        assert kwargs["courts"] == ["flmd", "flsd", "flnd"]
        if self.rd_hits is not None and kwargs["search_type"] == "rd":
            return {"success": True, "status_code": 200, "results": self.rd_hits}
        if self.search_result is not None and kwargs["search_type"] == "r":
            return self.search_result
        if kwargs["search_type"] == "r":
            return {"success": True, "status_code": 200, "results": self.hits}
        return {"success": True, "status_code": 200, "results": []}

    async def search_recent(self, **kwargs):
        self.calls.append(("search_recent", kwargs))
        assert int(kwargs.get("max_pages") or 1) <= 50
        assert kwargs.get("entry_date_filed_after")
        assert not kwargs.get("filed_after")
        assert int(kwargs.get("page_size") or 20) <= 20
        if kwargs["search_type"] in self.search_errors:
            return self.search_errors[kwargs["search_type"]]
        return await self.search_limited(
            search_type=kwargs["search_type"],
            query=kwargs["query"],
            courts=kwargs["courts"],
            page_size=kwargs.get("page_size") or 20,
        )

    async def get_docket_brief(self, docket_id: int):
        self.calls.append(("brief", docket_id))
        data = self.brief.get(docket_id) if isinstance(self.brief, dict) and docket_id in self.brief else self.brief
        return {"success": True, "status_code": 200, "data": data}

    async def list_docket_parties(self, docket_id: int, **kwargs):
        self.calls.append(("parties", docket_id))
        if self.parties_error and (
            self.parties_error_ids is None or docket_id in self.parties_error_ids
        ):
            return self.parties_error
        return {
            "success": True,
            "status_code": 200,
            "results": self.parties,
            "truncated": bool(self.parties_truncated),
        }

    async def list_docket_entries(self, docket_id: int, **kwargs):
        self.calls.append(("entries", docket_id))
        assert int(kwargs.get("max_pages") or 1) <= 50
        if self.entries_error and (
            self.entries_error_ids is None or docket_id in self.entries_error_ids
        ):
            return self.entries_error
        return {
            "success": True,
            "status_code": 200,
            "results": self.entries,
            "truncated": bool(self.entries_truncated),
        }

    async def close(self):
        return None


def _client(name="John Smith", dobs=(), numbers=(), bond="bond-1", defendant="def-1"):
    return ClientIdentity(
        bond_case_id=bond,
        defendant_id=defendant,
        names=(name,),
        dobs=frozenset(dobs),
        numbers=frozenset(numbers),
        status="active",
    )


def _hit(**overrides):
    row = {
        "id": 1001,
        "docket_id": 1001,
        "court_id": "flmd",
        "caseName": "United States v. John Smith",
        "docketNumber": "2:24-cr-00123",
        "recap_documents": [{"id": 9, "description": "Order Setting Conditions of Release"}],
    }
    row.update(overrides)
    return row


def _defendant(name="Smith, John", *, dob="", docket_id=1001, extra_info=None, party_id=None):
    party = {
        "name": name,
        "party_types": [{
            "docket_id": docket_id,
            "name": "Defendant",
            "extra_info": extra_info if extra_info is not None else (f"DOB: {dob}" if dob else ""),
        }],
    }
    if party_id is not None:
        party["id"] = party_id
    return party


def _enable(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")


async def _scan(db, court, clients, notifier=None):
    return await run_federal_bond_risk_scan(
        db,
        client=court,
        notifier=notifier or _Notifier(),
        pause_s=0,
        clients=clients,
    )


def test_search_query_covers_every_classified_phrase():
    from dashboard.services.federal_bond_risk import BOND_RISK_QUERY, classify_bond_risk, search_phrases

    examples = [
        "Bond increased to $50,000",
        "Bond reduced to $5,000",
        "Bond set at $10,000",
        "Appearance bond set",
        "Order setting conditions of release",
        "Conditions of release",
        "Detention hearing",
        "Failure to appear",
        "Failed to appear",
        "Arrest warrant issued",
        "Bench warrant",
        "Capias",
        "Revocation of release",
        "Revocation of pretrial release",
        "Bond revocation",
        "Motion to revoke bond",
        "Motion to modify release",
        "Order modifying the release",
        "Modified release",
        "Order modifying conditions of release",
        "Amended conditions of release",
    ]
    phrases = search_phrases()
    assert "bond revocation" in {phrase.lower() for phrase in phrases}
    assert "revocation of pretrial release" in {phrase.lower() for phrase in phrases}
    assert BOND_RISK_QUERY == " OR ".join(
        f'"{phrase}"' if " " in phrase else phrase for phrase in phrases
    )
    for text in examples:
        assert classify_bond_risk(text), text
        assert any(phrase.lower() in text.lower() for phrase in phrases), text
    for phrase in phrases:
        assert classify_bond_risk(phrase), phrase


def test_docket_number_strips_every_trailing_judge_initial():
    from dashboard.services.federal_bond_risk import (
        canonical_identifier,
        is_fl_federal_criminal,
        match_client,
    )

    base = canonical_identifier("2:24-cr-00123")
    assert canonical_identifier("2:24-cr-00123-SPC") == base
    assert canonical_identifier("2:24-cr-00123-JES-NPM") == base
    assert canonical_identifier("2:24-cr-00123-1-JES-NPM") == "2:24-CR-00123-1"
    assert canonical_identifier("9:20-mj-00015-AB-CD") == canonical_identifier("9:20-mj-00015")
    assert is_fl_federal_criminal("flmd", "2:24-cr-00123-JES-NPM")
    docket = _docket_for(DocketParty("Smith, John", frozenset()))
    docket.docket_number = "2:24-cr-00123-JES-NPM"
    assert match_client(_client(numbers=["2:24-cr-00123"]), docket) == ("confirmed", ("case_number",))

    # Agency numbers keep alphabetic suffixes intact so distinct agencies never collide.
    assert canonical_identifier("AG-445566-FL") == "AG-445566-FL"
    assert canonical_identifier("AG-445566-JES") == "AG-445566-JES"
    agency_docket = _docket_for(DocketParty("Smith, John", frozenset()))
    agency_docket.docket_number = "2:24-cr-00999"
    agency_docket.numbers = {"AG-445566-JES"}
    assert match_client(_client(numbers=["AG-445566-FL"]), agency_docket) == ("possible", ())


def test_subjectless_actor_event_on_one_defendant_is_not_confirmed():
    from dashboard.services.federal_bond_risk import decide_matches

    filing = DocketEvent("failure_to_appear", "Failure to appear", "entry:1")
    docket = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        events=[filing],
    )
    rows = decide_matches([_client(dobs=["1985-01-15"])], docket)
    assert rows[0].status == "possible"
    assert rows[0].confirmations == ()
    named = DocketEvent("failure_to_appear", "Failure to appear", "entry:2", ("John Smith",))
    linked = decide_matches([_client(dobs=["1985-01-15"])], _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        events=[named],
    ))
    assert linked[0].status == "confirmed"
    hearing = DocketEvent("detention_hearing", "Detention hearing", "entry:3")
    case_event = decide_matches([_client(dobs=["1985-01-15"])], _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        events=[hearing],
    ))
    assert case_event[0].status == "confirmed"


def test_subjectless_filing_on_several_defendants_is_not_confirmed():
    from dashboard.services.federal_bond_risk import decide_matches

    filing = DocketEvent("warrant", "Warrant", "entry:1")
    multi = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=[filing],
    )
    client = _client(name="John Smith", dobs=["1985-01-15"])
    rows = decide_matches([client], multi)
    assert rows[0].status == "possible"
    assert rows[0].confirmations == ()
    named = DocketEvent("warrant", "Warrant", "entry:2", ("John Smith",))
    linked = decide_matches([client], _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=[named],
    ))
    assert linked[0].status == "confirmed"


def test_two_bonds_for_one_defendant_are_not_an_ambiguous_match():
    from dashboard.services.federal_bond_risk import decide_matches

    docket = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        events=[DocketEvent("warrant", "Warrant", "entry:1", ("John Smith",))],
    )
    same_person = [
        _client(dobs=["1985-01-15"], bond="bond-a", defendant="def-1"),
        _client(dobs=["1985-01-15"], bond="bond-b", defendant="def-1"),
    ]
    rows = decide_matches(same_person, docket)
    assert [row.status for row in rows] == ["confirmed", "confirmed"]
    assert all(not row.ambiguous for row in rows)
    two_people = [
        _client(name="John Smith", dobs=["1985-01-15"], bond="bond-a", defendant="def-a"),
        _client(name="Jane Roe", dobs=["1990-02-02"], bond="bond-b", defendant="def-b"),
    ]
    mixed = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=[DocketEvent("warrant", "Warrant", "entry:9")],
    )
    # Subjectless on two defendants stays possible, so ambiguity is tested
    # with a filing that names both people.
    both = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=[DocketEvent("warrant", "Warrant", "entry:9", ("John Smith", "Jane Roe"))],
    )
    ambiguous = decide_matches(two_people, both)
    assert {row.status for row in ambiguous} == {"possible"}
    assert all(row.ambiguous for row in ambiguous)
    assert decide_matches(two_people, mixed)[0].status == "possible"


@pytest.mark.asyncio
async def test_upgrade_and_downgrade_write_audit_events(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[{"name": "Smith, John"}],
    )
    db = _DB()
    client = _client()
    await _scan(db, court, [client])
    recorded = [row for row in db.audit_events.docs if row["reason"] == "match_recorded"]
    assert recorded
    assert recorded[0]["actor"] == "system"
    assert recorded[0]["confidence"] == 0.5
    assert recorded[0]["new_state"]["match_status"] == "possible"
    assert recorded[0]["new_state"]["confidence"] == 0.5
    assert recorded[0]["evidence"]["filing_id"]
    assert recorded[0]["evidence"]["candidate_bond_case_id"] == client.bond_case_id

    court.parties = [_defendant(dob="01/15/1985")]
    await _scan(db, court, [_client(dobs=["1985-01-15"])])
    upgraded = [row for row in db.audit_events.docs if row["reason"] == "possible_upgraded_to_confirmed"]
    assert upgraded[0]["actor"] == "system"
    assert upgraded[0]["confidence"] == 0.95
    assert upgraded[0]["old_state"]["match_status"] == "possible"
    assert upgraded[0]["old_state"]["auto_linked"] is False
    assert upgraded[0]["old_state"]["confidence"] == 0.5
    assert upgraded[0]["new_state"]["match_status"] == "confirmed"
    assert upgraded[0]["new_state"]["auto_linked"] is True
    assert upgraded[0]["new_state"]["confidence"] == 0.95

    court.parties = [{
        "name": "Smith, John",
        "party_types": [
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 02/02/1990"},
        ],
    }]
    await _scan(db, court, [_client(dobs=["1985-01-15"])])
    downgraded = [row for row in db.audit_events.docs if row["reason"] == "identity_no_longer_confirms"]
    assert downgraded[0]["confidence"] == 0.5
    assert downgraded[0]["old_state"]["auto_linked"] is True
    assert downgraded[0]["old_state"]["confidence"] == 0.95
    assert downgraded[0]["new_state"]["match_status"] == "possible"
    assert downgraded[0]["new_state"]["auto_linked"] is False
    assert downgraded[0]["new_state"]["confidence"] == 0.5


@pytest.mark.asyncio
async def test_docket_entry_outage_is_not_an_empty_success(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
    )
    court.entries_error = {"success": False, "error": "HTTP 503", "status_code": 503, "results": []}
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["success"] is False
    assert result["skipped"] == "entry_hydration_failed"
    assert "503" in result["warning"]
    assert db.docket_events.docs == []

    partial = _CourtListener(
        hits=[
            _hit(id=1001, docket_id=1001),
            _hit(id=1002, docket_id=1002, docketNumber="2:24-cr-00456", recap_documents=[]),
        ],
        parties=[_defendant(dob="01/15/1985")],
    )
    partial.entries_error = {"success": False, "error": "bad_json", "status_code": 200, "results": []}
    partial.entries_error_ids = {1002}
    partial_db = _DB()
    kept = await _scan(partial_db, partial, [_client(dobs=["1985-01-15"])])
    assert kept["success"] is True
    assert kept["rows_written"] == 1
    assert "1002" in kept["warning"]


@pytest.mark.asyncio
async def test_reinstated_bond_restores_the_withdrawn_row(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import run_federal_bond_risk_scan

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "status": "active",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "date_of_birth": "1985-01-15",
    })
    court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    notifier = _Notifier()
    first = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    assert first["confirmed"] == 1
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert db.docket_events.docs[0]["bond_case_id"] == "bond-1"

    db.active_bonds.docs[0]["status"] = "exonerated"
    await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    withdrawn = db.docket_events.docs[0]
    assert withdrawn["match_status"] == "withdrawn"
    assert withdrawn["auto_linked"] is False
    assert withdrawn["bond_case_id"] == ""

    db.active_bonds.docs[0]["status"] = "reinstated"
    restored = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    row = db.docket_events.docs[0]
    assert restored["confirmed"] == 1
    assert len(db.docket_events.docs) == 1
    assert row["match_status"] == "confirmed"
    assert row["auto_linked"] is True
    assert row["bond_case_id"] == "bond-1"
    assert row["matched_defendant_id"] == "def-1"
    assert row["confirmations"] == ["dob"]
    assert row["match_history"][-1]["from"] == "withdrawn"
    assert row["match_history"][-1]["to"] == "confirmed"
    assert any(event["reason"] == "withdrawn_restored" for event in db.audit_events.docs)
    assert notifier.calls[-1]["details"]["match_status"] == "confirmed"
    assert notifier.calls[-1]["details"]["auto_linked"] is True
    assert notifier.calls[-1]["details"]["bond_case_id"] == "bond-1"


@pytest.mark.asyncio
async def test_slack_posts_the_claimed_row_not_the_snapshot(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, retry_pending_slack

    db = _DB()
    db.docket_events.docs.append({
        "_id": "race",
        "source": SOURCE,
        "source_id": "fl-federal:1:warrant:entry:1:bond-1",
        "slack_delivered": False,
        "slack_claim": False,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for John Smith.",
        "match_status": "confirmed",
        "auto_linked": True,
        "notification_revision": 1,
        "bond_case_id": "bond-1",
        "event_type": "warrant",
        "filing_id": "entry:1",
        "court_name": "M.D. Florida",
        "docket_number": "2:24-cr-00123",
    })

    class _DowngradeDuringClaim(_Collection):
        async def update_one(self, filt, update, upsert=False):
            if update.get("$set", {}).get("slack_claim") is True:
                self.docs[0]["match_status"] = "possible"
                self.docs[0]["auto_linked"] = False
                self.docs[0]["notification_revision"] = 2
                self.docs[0]["title"] = "Federal bond risk needs review — Warrant"
                self.docs[0]["description"] = "Possible match for John Smith."
                self.docs[0]["slack_claim"] = False
                self.docs[0]["slack_delivered"] = False
            return await super().update_one(filt, update, upsert=upsert)

    db.docket_events = _DowngradeDuringClaim()
    db.docket_events.docs.append({
        "_id": "race",
        "source": SOURCE,
        "source_id": "fl-federal:1:warrant:entry:1:bond-1",
        "slack_delivered": False,
        "slack_claim": False,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for John Smith.",
        "match_status": "confirmed",
        "auto_linked": True,
        "notification_revision": 1,
        "bond_case_id": "bond-1",
        "event_type": "warrant",
        "filing_id": "entry:1",
        "court_name": "M.D. Florida",
        "docket_number": "2:24-cr-00123",
    })
    raced = _Notifier()
    sent, pending, attempted = await retry_pending_slack(db, raced)
    assert sent == 0 and attempted == 0
    assert raced.calls == []
    assert db.docket_events.docs[0]["match_status"] == "possible"

    class _RewriteDuringClaim(_Collection):
        async def update_one(self, filt, update, upsert=False):
            if update.get("$set", {}).get("slack_claim") is True:
                self.docs[0]["title"] = "Claimed title"
                self.docs[0]["description"] = "Posted from the claimed row."
            return await super().update_one(filt, update, upsert=upsert)

    fresh = _DB()
    fresh.docket_events = _RewriteDuringClaim()
    fresh.docket_events.docs.append({
        "_id": "current",
        "source": SOURCE,
        "source_id": "fl-federal:1:warrant:entry:2:bond-1",
        "slack_delivered": False,
        "slack_claim": False,
        "title": "Old snapshot title",
        "description": "Old snapshot body.",
        "match_status": "confirmed",
        "auto_linked": True,
        "notification_revision": 1,
        "bond_case_id": "bond-1",
        "event_type": "warrant",
        "filing_id": "entry:2",
        "court_name": "M.D. Florida",
        "docket_number": "2:24-cr-00123",
    })
    posted = _Notifier()
    sent, _pending, attempted = await retry_pending_slack(fresh, posted)
    assert sent == 1 and attempted == 1
    assert posted.calls[0]["title"] == "Claimed title"
    assert posted.calls[0]["message"] == "Posted from the claimed row."
    assert posted.calls[0]["details"]["match_status"] == "confirmed"


@pytest.mark.asyncio
async def test_reinstated_bond_stays_monitored_and_linked(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, run_federal_bond_risk_scan

    db = _DB()
    db.active_bonds.docs.extend([
        {
            "_id": "bond-re",
            "status": "reinstated",
            "Bond_Case_ID": "bond-re",
            "Defendant_ID": "def-re",
            "defendant_name": "John Smith",
        },
        {
            "_id": "bond-ex",
            "status": "exonerated",
            "Bond_Case_ID": "bond-ex",
            "Defendant_ID": "def-ex",
            "defendant_name": "Jane Roe",
        },
    ])
    clients = await load_active_clients(db)
    assert [client.bond_case_id for client in clients] == ["bond-re"]
    for source_id, bond_id in (
        ("fl-federal:1:warrant:entry:1:bond-re", "bond-re"),
        ("fl-federal:1:warrant:entry:1:bond-ex", "bond-ex"),
    ):
        db.docket_events.docs.append({
            "_id": bond_id,
            "source": SOURCE,
            "source_id": source_id,
            "slack_delivered": True,
            "match_status": "confirmed",
            "auto_linked": True,
            "bond_case_id": bond_id,
            "event_type": "warrant",
            "filing_id": "entry:1",
            "docket_id": "1",
        })
    await run_federal_bond_risk_scan(
        db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0,
    )
    by_bond = {row["prior_bond_case_id"] if row.get("match_status") == "withdrawn" else row["bond_case_id"]: row for row in db.docket_events.docs}
    assert by_bond["bond-re"]["match_status"] == "confirmed"
    assert by_bond["bond-re"]["auto_linked"] is True
    assert by_bond["bond-ex"]["match_status"] == "withdrawn"
    assert by_bond["bond-ex"]["auto_linked"] is False


@pytest.mark.asyncio
async def test_failed_match_audit_is_written_on_the_next_scan(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    real_update = db.audit_events.update_one
    state = {"calls": 0}

    async def fail_once(filt, update, upsert=False):
        state["calls"] += 1
        if state["calls"] == 1 and upsert:
            raise RuntimeError("audit down")
        return await real_update(filt, update, upsert=upsert)

    db.audit_events.update_one = fail_once
    await _scan(db, court, [_client()])
    assert db.docket_events.docs[0]["match_status"] == "possible"
    assert db.audit_events.docs == []
    assert db.docket_events.docs[0]["audit_pending"]
    await _scan(db, court, [_client()])
    assert len(db.docket_events.docs) == 1
    assert len(db.audit_events.docs) == 1
    assert db.audit_events.docs[0]["actor"] == "system"
    assert db.audit_events.docs[0]["reason"] == "match_recorded"
    assert db.docket_events.docs[0]["audit_pending"] == []


@pytest.mark.asyncio
async def test_party_type_dob_for_this_docket_confirms_the_client(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[{
            "name": "Smith, John",
            "extra_info": "DOB: 03/03/1970",
            "party_types": [
                {
                    "docket": "https://www.courtlistener.com/api/rest/v4/dockets/9999/",
                    "docket_id": 9999,
                    "name": "Defendant",
                    "extra_info": "DOB: 02/02/1990",
                },
                {
                    "docket": "https://www.courtlistener.com/api/rest/v4/dockets/1001/",
                    "docket_id": 1001,
                    "name": "Defendant",
                    "extra_info": "DOB: 01/15/1985",
                },
            ],
        }],
    )
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["confirmed"] == 1
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert db.docket_events.docs[0]["confirmations"] == ["dob"]

    other = _CourtListener(
        hits=[_hit()],
        parties=[{
            "name": "Smith, John",
            "extra_info": "DOB: 01/15/1985",
            "party_types": [
                {"docket_id": 9999, "extra_info": "DOB: 01/15/1985"},
                {"docket_id": 1001, "extra_info": ""},
            ],
        }],
    )
    other_db = _DB()
    await _scan(other_db, other, [_client(dobs=["1985-01-15"])])
    assert other_db.docket_events.docs[0]["match_status"] == "possible"
    assert other_db.docket_events.docs[0]["auto_linked"] is False


@pytest.mark.asyncio
async def test_upgrade_keeps_an_earlier_pending_audit(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    real_update = db.audit_events.update_one
    state = {"calls": 0}

    async def fail_until_retry(filt, update, upsert=False):
        state["calls"] += 1
        if state["calls"] <= 3 and upsert:
            raise RuntimeError("audit down")
        return await real_update(filt, update, upsert=upsert)

    db.audit_events.update_one = fail_until_retry
    await _scan(db, court, [_client()])
    original = db.docket_events.docs[0]["audit_pending"][0]["event_id"]
    court.parties = [_defendant(dob="01/15/1985")]
    await _scan(db, court, [_client(dobs=["1985-01-15"])])
    pending_ids = [item["event_id"] for item in db.docket_events.docs[0]["audit_pending"]]
    assert original in pending_ids
    assert len(pending_ids) == 2
    assert db.audit_events.docs == []
    await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert len(db.docket_events.docs) == 1
    assert {row["event_id"] for row in db.audit_events.docs} == set(pending_ids)
    assert db.docket_events.docs[0]["audit_pending"] == []


@pytest.mark.asyncio
async def test_each_match_transition_notifies_once(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    client = _client(dobs=["1985-01-15"])
    await _scan(db, court, [client])
    court.parties = [_defendant(dob="01/15/1985")]
    await _scan(db, court, [client])
    court.parties = [{
        "name": "Smith, John",
        "party_types": [
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 02/02/1990"},
        ],
    }]
    await _scan(db, court, [client])
    keys = [row["claim_key"] for row in db.notifications.docs]
    assert len(keys) == 3
    assert len(set(keys)) == 3
    assert any(key.endswith(":possible:0") for key in keys)
    assert any(key.endswith(":confirmed:1") for key in keys)
    assert any(key.endswith(":possible:2") for key in keys)
    await _scan(db, court, [client])
    assert len(db.notifications.docs) == 3


@pytest.mark.asyncio
async def test_possible_row_is_withdrawn_when_its_candidate_bond_closes(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, run_federal_bond_risk_scan

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-open",
        "status": "active",
        "Bond_Case_ID": "bond-open",
        "Defendant_ID": "def-open",
        "defendant_name": "John Smith",
    })
    db.docket_events.docs.extend([
        {
            "_id": "possible-closed",
            "source": SOURCE,
            "source_id": "fl-federal:9:warrant:entry:1:bond-closed",
            "slack_delivered": False,
            "title": "Federal bond risk needs review — Warrant",
            "description": "Possible match for Pat Sample.",
            "court_name": "M.D. Florida",
            "docket_number": "9:20-cr-00001",
            "event_type": "warrant",
            "filing_id": "entry:1",
            "match_status": "possible",
            "auto_linked": False,
            "bond_case_id": "",
            "candidate_bond_case_id": "bond-closed",
        },
        {
            "_id": "possible-open",
            "source": SOURCE,
            "source_id": "fl-federal:9:warrant:entry:2:bond-open",
            "slack_delivered": True,
            "title": "Federal bond risk needs review — Warrant",
            "description": "Possible match for John Smith.",
            "match_status": "possible",
            "auto_linked": False,
            "bond_case_id": "",
            "candidate_bond_case_id": "bond-open",
            "event_type": "warrant",
            "filing_id": "entry:2",
        },
    ])
    notifier = _Notifier()
    await run_federal_bond_risk_scan(
        db, client=_CourtListener(hits=[]), notifier=notifier, pause_s=0,
    )
    closed = next(row for row in db.docket_events.docs if row["_id"] == "possible-closed")
    opened = next(row for row in db.docket_events.docs if row["_id"] == "possible-open")
    assert closed["match_status"] == "withdrawn"
    assert closed["auto_linked"] is False
    assert opened["match_status"] == "possible"
    assert notifier.calls
    assert notifier.calls[0]["details"]["match_status"] == "withdrawn"
    assert notifier.calls[0]["details"]["auto_linked"] is False


@pytest.mark.asyncio
async def test_closed_bond_past_the_first_page_is_still_unlinked(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, run_federal_bond_risk_scan

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "open-bond",
        "status": "active",
        "Bond_Case_ID": "bond-open",
        "Defendant_ID": "def-open",
        "defendant_name": "John Smith",
    })
    for index in range(501):
        db.docket_events.docs.append({
            "_id": f"{index:04d}",
            "source": SOURCE,
            "source_id": f"fl-federal:1:warrant:entry:{index}:bond-open",
            "slack_delivered": True,
            "match_status": "confirmed",
            "auto_linked": True,
            "bond_case_id": "bond-open",
            "event_type": "warrant",
            "filing_id": f"entry:{index}",
            "docket_id": "1",
        })
    db.docket_events.docs.append({
        "_id": "zzz-closed",
        "source": SOURCE,
        "source_id": "fl-federal:1:warrant:entry:closed:bond-closed",
        "slack_delivered": False,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for Pat Sample.",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-closed",
        "event_type": "warrant",
        "filing_id": "entry:closed",
        "docket_id": "1",
        "court_name": "M.D. Florida",
        "docket_number": "9:20-cr-00001",
    })
    notifier = _Notifier()
    await run_federal_bond_risk_scan(
        db, client=_CourtListener(hits=[]), notifier=notifier, pause_s=0,
    )
    closed = next(row for row in db.docket_events.docs if row["_id"] == "zzz-closed")
    assert closed["match_status"] == "withdrawn"
    assert closed["auto_linked"] is False
    assert notifier.calls
    assert notifier.calls[0]["details"]["auto_linked"] is False


def test_full_name_and_event_rules():
    assert full_name_equal("John Adam Smith", "SMITH, JOHN ADAM")
    assert full_name_equal("John Smith Jr.", "SMITH, JOHN")
    assert full_name_equal("John Smith", "United States v. John Smith") is False
    assert full_name_equal("John Smith", "John Brian Smith")
    assert not full_name_equal("John Adam Smith", "John Brian Smith")
    assert not full_name_equal("J Smith", "Smith, J")
    assert not full_name_equal("Smith", "John Smith")
    assert full_name_equal("John De La Cruz", "De La Cruz, John")
    assert full_name_equal("John Adam van der Berg", "van der Berg, John Adam")
    assert not full_name_equal("John De La Cruz", "John Cruz")

    assert [item[0] for item in classify_bond_risk("Order Setting Conditions of Release")] == ["bond_conditions_set"]
    assert [item[0] for item in classify_bond_risk("Order Modifying Conditions of Release")] == ["modification_of_release"]
    assert "bond_conditions_changed" in [item[0] for item in classify_bond_risk("Amended Conditions of Release")]
    assert [item[0] for item in classify_bond_risk("Detention hearing set for Monday")] == ["detention_hearing"]
    assert [item[0] for item in classify_bond_risk("Failure to appear")] == ["failure_to_appear"]
    assert [item[0] for item in classify_bond_risk("Arrest warrant issued")] == ["warrant"]
    assert [item[0] for item in classify_bond_risk("Revocation of pretrial release")] == ["revocation_of_release"]
    assert classify_bond_risk("Search warrant for a residence") == []
    assert classify_bond_risk("United States v. John Smith") == []


@pytest.mark.asyncio
async def test_flag_off_does_not_call_courtlistener_or_write(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")
    court = _CourtListener(hits=[_hit()])
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["skipped"] == "flag_off"
    assert result["enabled"] is False
    assert result["client_messages"] == 0
    assert result["rows_written"] == 0
    assert court.calls == []
    assert db.notifications.docs == []
    assert db.docket_events.docs == []


@pytest.mark.asyncio
async def test_missing_token_skips_with_warning_and_no_http(monkeypatch):
    monkeypatch.setenv(FLAG, "true")
    constructed = []

    class _Boom:
        def __init__(self, *args, **kwargs):
            constructed.append(True)
            raise AssertionError("CourtListener client constructed without a token")

    monkeypatch.setattr("dashboard.services.courtlistener_client.CourtListenerClient", _Boom)
    db = _DB()
    result = await run_federal_bond_risk_scan(db, pause_s=0)
    assert result["skipped"] == "no_token"
    assert result["token_configured"] is False
    assert "401" in result["warning"]
    assert "rejected" in result["warning"].lower()
    assert result["rows_written"] == 0
    assert result["client_messages"] == 0
    assert constructed == []
    assert db.notifications.docs == []


@pytest.mark.asyncio
async def test_name_and_dob_confirms_and_alerts_staff_only(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        brief={
            "id": 1001,
            "court_id": "flmd",
            "docket_number": "2:24-cr-00123-SPC",
            "case_name": "United States v. John Smith",
            "pacer_case_id": "999111",
        },
        parties=[_defendant(dob="01/15/1985", party_id=1)],
        entries=[{"id": 5, "description": "Detention Hearing", "date_filed": "2026-11-01"}],
    )
    notifier = _Notifier()
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier)
    assert result["skipped"] is None
    assert result["confirmed"] == 2
    assert result["possible"] == 0
    assert result["client_messages"] == 0
    assert result["slack_sent"] == 2
    assert {row["event_type"] for row in db.docket_events.docs} == {
        "bond_conditions_set",
        "detention_hearing",
    }
    for row in db.docket_events.docs:
        assert row["match_status"] == "confirmed"
        assert row["auto_linked"] is True
        assert row["matched_defendant_id"] == "def-1"
        assert row["bond_case_id"] == "bond-1"
        assert "1985-01-15" not in json.dumps(row)
        assert "01/15/1985" not in json.dumps(row)
    assert {note["type"] for note in db.notifications.docs} == {"federal_bond_risk"}
    for note in db.notifications.docs:
        assert note["metadata"]["match_status"] == "confirmed"
        assert "Linked" in note["message"]
    for call in notifier.calls:
        assert call["bridge"] is False
        assert call["source"] == "FederalBondRisk"
        blob = json.dumps(call)
        assert "1985-01-15" not in blob
        assert "01/15/1985" not in blob
        assert call["details"]["auto_linked"] is True


@pytest.mark.asyncio
async def test_full_name_alone_is_possible_and_not_linked(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"description": "Failure to appear"}])],
        brief={"id": 1001, "court_id": "flsd", "docket_number": "1:24-cr-00088", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John", "extra_info": ""}],
        entries=[],
    )
    court.hits[0]["court_id"] = "flsd"
    court.hits[0]["docketNumber"] = "1:24-cr-00088"
    db = _DB()
    notifier = _Notifier()
    result = await _scan(db, court, [_client()], notifier)
    assert result["possible"] == 1
    assert result["confirmed"] == 0
    row = db.docket_events.docs[0]
    assert row["match_status"] == "possible"
    assert row["auto_linked"] is False
    assert row["matched_defendant_id"] is None
    assert row["bond_case_id"] == ""
    assert row["candidate_bond_case_id"] == "bond-1"
    assert "not auto-linked" in row["description"].lower() or "Staff review" in row["description"]
    assert notifier.calls[0]["details"]["auto_linked"] is False
    assert db.notifications.docs[0]["priority"] == "high"


@pytest.mark.asyncio
async def test_surname_only_and_dob_conflict_do_not_link(monkeypatch):
    _enable(monkeypatch)
    surname = _CourtListener(
        hits=[_hit(caseName="United States v. Smith")],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. Smith"},
        parties=[{"name": "Smith", "extra_info": "DOB: 01/15/1985"}],
        entries=[{"description": "Arrest warrant issued"}],
    )
    db = _DB()
    result = await _scan(db, surname, [_client(dobs=["1985-01-15"])])
    assert result["rows_written"] == 0
    assert db.notifications.docs == []

    conflict = _CourtListener(
        hits=[_hit()],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith", "pacer_case_id": "999111"},
        parties=[{"name": "Smith, John", "extra_info": "DOB: 02/02/1990"}],
        entries=[{"description": "Bench warrant issued"}],
    )
    db = _DB()
    result = await _scan(
        db,
        conflict,
        [_client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])],
    )
    assert result["rows_written"] == 0
    assert db.docket_events.docs == []


@pytest.mark.asyncio
async def test_case_number_confirms_without_a_docket_dob(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"description": "Bond increased"}])],
        brief={"id": 1001, "court_id": "flnd", "docket_number": "2:24-cr-00123-SPC", "case_name": "United States v. John Smith"},
        parties=[_defendant("John Smith")],
        entries=[],
    )
    db = _DB()
    result = await _scan(db, court, [_client(numbers=["2:24-cr-00123"])])
    assert result["confirmed"] == 1
    row = db.docket_events.docs[0]
    assert row["confirmations"] == ["case_number"]
    assert row["court_id"] == "flnd"
    assert row["auto_linked"] is True
    assert "case or agency number" in row["description"]


@pytest.mark.asyncio
async def test_two_confirmed_clients_stay_unlinked(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"description": "Detention hearing"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[_defendant(dob="1985-01-15")],
        entries=[],
    )
    clients = [
        _client(dobs=["1985-01-15"], bond="bond-a", defendant="def-a"),
        _client(dobs=["1985-01-15"], bond="bond-b", defendant="def-b"),
    ]
    db = _DB()
    result = await _scan(db, court, clients)
    assert result["confirmed"] == 0
    assert result["possible"] == 2
    assert {row["candidate_bond_case_id"] for row in db.docket_events.docs} == {"bond-a", "bond-b"}
    assert all(row["auto_linked"] is False and row["ambiguous"] is True for row in db.docket_events.docs)


@pytest.mark.asyncio
async def test_civil_and_non_florida_dockets_are_ignored(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(hits=[
        _hit(id=2002, docket_id=2002, docketNumber="2:24-cv-01000", caseName="United States v. John Smith"),
        _hit(id=2003, docket_id=2003, court_id="gand", docketNumber="1:24-cr-00001", caseName="United States v. John Smith"),
    ])
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["rows_written"] == 0
    assert any(call[0] == "search" for call in court.calls)
    assert [call for call in court.calls if call[0] == "brief"] == []


@pytest.mark.asyncio
async def test_unauthorized_stops_before_any_write(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(search_result={
        "success": False,
        "status_code": 401,
        "error": "unauthorized",
        "warning": "CourtListener returned 401.",
        "results": [],
    })
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["skipped"] == "unauthorized"
    assert "401" in result["warning"]
    assert result["rows_written"] == 0
    assert db.notifications.docs == []
    assert not any(call[0] == "parties" for call in court.calls)


@pytest.mark.asyncio
async def test_duplicate_scan_does_not_alert_twice_and_possible_can_upgrade(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"description": "Detention hearing"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[],
    )
    db = _DB()
    notifier = _Notifier()
    first = await _scan(db, court, [_client()], notifier)
    second = await _scan(db, court, [_client()], notifier)
    assert first["rows_written"] == 1
    assert second["rows_written"] == 0
    assert len(notifier.calls) == 1
    assert len(db.notifications.docs) == 1

    court.parties = [_defendant(dob="01/15/1985")]
    upgraded = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier)
    assert upgraded["rows_written"] == 1
    assert upgraded["confirmed"] == 1
    assert len(db.docket_events.docs) == 1
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert len(db.notifications.docs) == 2
    assert len(notifier.calls) == 2


@pytest.mark.asyncio
async def test_loader_ignores_booking_numbers_and_phones(monkeypatch):
    _enable(monkeypatch)
    db = _DB()
    db.active_bonds.docs.append({
        "Bond_Case_ID": "bond-9",
        "status": "active",
        "defendant_name": "Jane Roe",
        "Defendant_ID": "def-9",
        "booking_number": "2:24-cr-00999",
        "phone": "239-555-0199",
        "case_number": "2024-CF-001234",
    })
    db.active_bonds.docs.append({
        "Bond_Case_ID": "bond-closed",
        "status": "exonerated",
        "defendant_name": "Jane Roe",
        "Defendant_ID": "def-closed",
    })
    db.defendants.docs.append({
        "Defendant_ID": "def-9",
        "full_name": "Roe, Jane",
        "date_of_birth": "1991-04-04",
    })
    db.arrests.docs.append({
        "Defendant_ID": "def-9",
        "agency_case_number": "AG-445566",
        "phone": "239-555-0199",
    })
    clients = await load_active_clients(db)
    assert len(clients) == 1
    assert clients[0].dobs == frozenset({"1991-04-04"})
    assert "2:24-CR-00999" not in clients[0].numbers
    assert "2024-CF-001234" in clients[0].numbers
    assert "AG-445566" in clients[0].numbers

    court = _CourtListener(
        hits=[_hit(
            id=3003,
            docket_id=3003,
            caseName="United States v. Jane Roe",
            docketNumber="8:24-cr-00077",
            recap_documents=[{"description": "Conditions of release"}],
        )],
        brief={"id": 3003, "court_id": "flmd", "docket_number": "8:24-cr-00077", "case_name": "United States v. Jane Roe"},
        parties=[_defendant("Roe, Jane", dob="04/04/1991", docket_id=3003)],
        entries=[],
    )
    notifier = _Notifier()
    result = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    assert result["confirmed"] == 1
    blob = json.dumps(db.notifications.docs) + json.dumps(notifier.calls)
    assert "239-555-0199" not in blob
    assert "555" not in blob


@pytest.mark.asyncio
async def test_real_alert_path_posts_only_to_alerts_webhook(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("SLACK_WEBHOOK_ALERTS", "https://hooks.slack.com/services/TEST/HOOK")
    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append({"url": url, "body": json})
        if "hooks.slack.com" not in str(url):
            raise AssertionError(url)
        response = type("R", (), {"status_code": 200, "text": "ok"})()
        return response

    monkeypatch.setattr("writers.slack_notifier.requests.post", fake_post)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"description": "Capias issued"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "John Smith"}],
        entries=[],
    )
    db = _DB()
    result = await run_federal_bond_risk_scan(db, client=court, pause_s=0, clients=[_client()])
    assert result["slack_sent"] == 1
    assert posted[0]["url"] == "https://hooks.slack.com/services/TEST/HOOK"
    assert len(posted) == 1
    assert result["client_messages"] == 0


@pytest.mark.asyncio
async def test_client_uses_field_selection_backoff_and_one_page(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("dashboard.services.courtlistener_client.asyncio.sleep", fake_sleep)
    calls = []

    class _Response:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            if url == "/search/" and len([c for c in calls if c[0] == "/search/"]) == 1:
                return _Response(429, {"detail": "throttled"}, {"Retry-After": "0"})
            if url == "/search/":
                return _Response(200, {
                    "count": 1,
                    "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=more",
                    "results": [{"id": 1}],
                })
            if url == "/docket-entries/":
                return _Response(200, {"next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?page=2", "results": []})
            if url == "/parties/":
                return _Response(200, {"results": []})
            if url.startswith("/dockets/"):
                return _Response(200, {"id": 5, "court_id": "flmd", "docket_number": "2:24-cr-00001"})
            raise AssertionError(url)

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()
    found = await client.search_limited(search_type="r", query="detention", courts=["flmd"], page_size=50)
    assert found["success"] is True
    assert sleeps and sleeps[0] >= 2
    search_calls = [call for call in calls if call[0] == "/search/"]
    assert len(search_calls) == 2
    assert search_calls[-1][1]["page_size"] == 20
    assert all("cursor" not in call[0] for call in calls)

    await client.list_docket_entries(5, page_size=100)
    await client.list_docket_parties(5)
    await client.get_docket_brief(5)
    entry_params = [params for url, params in calls if url == "/docket-entries/"][0]
    party_params = [params for url, params in calls if url == "/parties/"][0]
    brief_params = [params for url, params in calls if url.startswith("/dockets/")][0]
    assert entry_params["fields"] == DOCKET_ENTRY_FIELDS
    assert "plain_text" not in entry_params["fields"]
    assert entry_params["page_size"] == 20
    assert party_params["fields"] == DOCKET_PARTY_FIELDS
    assert brief_params["fields"] == DOCKET_BRIEF_FIELDS
    assert len([url for url, _params in calls if url == "/docket-entries/"]) == 1

    bare = CourtListenerClient(api_token="")
    bare._client = _HTTP()
    skipped = await bare.search_limited(search_type="d", query="bond", courts=["flmd"])
    assert skipped["skipped"] == "no_token"
    assert skipped["status_code"] == 401


def test_env_example_token_placeholder_is_empty():
    text = (ROOT / ".env.example").read_text()
    token_lines = [line for line in text.splitlines() if line.startswith("COURTLISTENER_API_TOKEN=")]
    flag_lines = [line for line in text.splitlines() if line.startswith("FEDERAL_BOND_RISK_ALERTS=")]
    assert token_lines == ["COURTLISTENER_API_TOKEN="]
    assert flag_lines == ["FEDERAL_BOND_RISK_ALERTS="]
    window = text.lower().split("courtlistener api", 1)[1][:800]
    assert "anonymous" in window
    assert "rejected" in window


def test_service_does_not_message_clients():
    source = (ROOT / "dashboard/services/federal_bond_risk.py").read_text().lower()
    for banned in ("twilio", "bluebubbles", "imessage", "send_email", "send_sms", "smtp"):
        assert banned not in source


def test_cron_and_service_control_register_the_job():
    from dashboard.cron import CRON_REGISTRY
    from dashboard.routers.automation_control import ALL_SERVICE_KEYS, SERVICE_META

    from dashboard.services.automation_config import DEFAULT_CONFIG
    from dashboard.services.federal_bond_risk import federal_bond_risk_enabled

    job = next(row for row in CRON_REGISTRY if row.name == "federal_bond_risk")
    assert job.interval == 21600
    assert "federal_bond_risk" in ALL_SERVICE_KEYS
    assert "federal_bond_risk" in SERVICE_META
    assert DEFAULT_CONFIG["federal_bond_risk"]["enabled"] is True
    assert federal_bond_risk_enabled() is False


def test_status_route_reports_the_token_warning(monkeypatch):
    from dashboard.routers.docket_monitor_api import docket_monitor_bp

    db = _DB()
    db.docket_events.docs.append({
        "_id": "row-1",
        "source": "courtlistener_federal_bond_risk",
        "match_status": "possible",
        "defendant_name": "John Smith",
        "detected_at": "2026-11-19T00:00:00+00:00",
        "description": "Possible match for staff review.",
    })
    monkeypatch.setattr("dashboard.routers.docket_monitor_api.get_db", lambda: db)
    app = FastAPI()
    app.include_router(docket_monitor_bp)
    response = TestClient(app).get("/api/docket-monitor/federal-bond-risk")
    body = response.json()
    assert response.status_code == 200
    assert body["enabled"] is False
    assert body["token_configured"] is False
    assert "401" in body["warning"]
    assert body["count"] == 1
    assert body["alerts"][0]["match_status"] == "possible"


def _docket_for(*parties: DocketParty, events=None) -> DocketView:
    return DocketView(
        docket_id="1001",
        court_id="flmd",
        court_name="M.D. Florida",
        docket_number="2:24-cr-00123",
        case_name="United States v. John Smith",
        pacer_case_id="",
        cause="",
        names=["John Smith", "Jane Roe", *[party.name for party in parties]],
        parties=list(parties),
        events=events or [DocketEvent("warrant", "Warrant", "entry:1")],
    )


def test_dob_confirms_only_the_party_it_belongs_to():
    john = DocketParty("Smith, John", frozenset({"1985-01-15"}))
    jane = DocketParty("Roe, Jane", frozenset({"1990-02-02"}))
    docket = _docket_for(john, jane)

    wrong_party = match_client(_client(dobs=["1990-02-02"]), docket)
    assert wrong_party[0] is None

    bare_john = DocketParty("Smith, John", frozenset())
    borrowed = match_client(_client(dobs=["1990-02-02"]), _docket_for(bare_john, jane))
    assert borrowed == ("possible", ())

    own = match_client(_client(dobs=["1985-01-15"]), docket)
    assert own == ("confirmed", ("dob",))


@pytest.mark.asyncio
async def test_scan_does_not_auto_link_another_partys_dob(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[
            {"name": "Smith, John", "extra_info": ""},
            {"name": "Roe, Jane", "extra_info": "DOB: 01/15/1985"},
        ],
        entries=[],
    )
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["confirmed"] == 0
    assert result["possible"] == 1
    assert db.docket_events.docs[0]["auto_linked"] is False
    assert db.docket_events.docs[0]["confirmations"] == []


@pytest.mark.asyncio
async def test_recent_search_walks_a_bounded_number_of_pages(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("dashboard.services.courtlistener_client.asyncio.sleep", fake_sleep)
    calls = []

    class _Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            if "cursor=p4" in url:
                raise AssertionError("paging passed the cap")
            if url == "/search/":
                return _Response({
                    "results": [{"id": 1, "docket_id": 1, "dateFiled": "2026-11-18"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=p2",
                })
            if "cursor=p2" in url:
                return _Response({
                    "results": [{"id": 2, "docket_id": 2, "dateFiled": "2026-11-10"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=p3",
                })
            if "cursor=p3" in url:
                return _Response({
                    "results": [{"id": 3, "docket_id": 3}],
                    "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=p4",
                })
            if url == "/docket-entries/" and len([item for item in calls if item[0] == "/docket-entries/"]) == 1:
                return _Response({
                    "results": [{"id": 8, "description": "Detention hearing", "date_filed": "2026-11-18"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=e2",
                })
            if "cursor=e2" in url:
                return _Response({
                    "results": [{"id": 7, "description": "Older note", "date_filed": "2026-10-01"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=e3",
                })
            raise AssertionError(url)

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()
    found = await client.search_recent(
        search_type="r",
        query="detention",
        courts=["flmd", "flsd", "flnd"],
        entry_date_filed_after="2026-10-06",
        page_size=50,
        max_pages=3,
        pause_s=0,
    )
    assert found["pages_fetched"] == 3
    assert found["truncated"] is True
    assert [row["id"] for row in found["results"]] == [1, 2, 3]
    first = calls[0][1]
    assert first["entry_date_filed_after"] == "2026-10-06"
    assert "filed_after" not in first
    assert first["order_by"] == "entry_date_filed desc"
    assert first["page_size"] == 20
    assert len([url for url, _params in calls if "cursor=p" in url or url == "/search/"]) == 3
    assert not any("cursor=p4" in url for url, _params in calls)

    entries = await client.list_docket_entries(5, page_size=20, max_pages=2, pause_s=0)
    assert entries["pages_fetched"] == 2
    assert [row["id"] for row in entries["results"]] == [8, 7]
    entry_params = [params for url, params in calls if url == "/docket-entries/"][0]
    assert entry_params["fields"] == DOCKET_ENTRY_FIELDS
    assert "plain_text" not in entry_params["fields"]
    assert not any("cursor=e3" in url for url, _params in calls)


@pytest.mark.asyncio
async def test_distinct_filings_of_the_same_type_each_alert_once(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    old = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[_defendant(dob="01/15/1985")],
        entries=[
            {"id": 11, "description": "Detention hearing", "date_filed": today},
            {"id": 12, "description": "Detention hearing", "date_filed": today},
            {"id": 13, "description": "Detention hearing", "date_filed": old},
        ],
    )
    db = _DB()
    notifier = _Notifier()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier)
    assert result["rows_written"] == 2
    filings = {row["filing_id"] for row in db.docket_events.docs}
    assert filings == {"entry:11", "entry:12"}
    assert all(row["alert_complete"] is True for row in db.docket_events.docs)
    again = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier)
    assert again["rows_written"] == 0
    assert len(db.notifications.docs) == 2
    assert len(notifier.calls) == 2


@pytest.mark.asyncio
async def test_slack_failure_retries_without_a_second_crm_row(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Failure to appear"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[],
    )

    class _Flaky:
        def __init__(self):
            self.calls = []

        def notify_alert(self, **kwargs):
            self.calls.append(kwargs)
            assert kwargs["bridge"] is False
            return len(self.calls) > 1

    notifier = _Flaky()
    db = _DB()
    clients = [_client()]
    first = await _scan(db, court, clients, notifier)
    assert first["rows_written"] == 1
    assert first["slack_sent"] == 0
    assert first["slack_pending"] == 1
    assert db.docket_events.docs[0]["slack_delivered"] is False
    assert db.docket_events.docs[0]["alert_complete"] is False
    assert len(db.notifications.docs) == 1

    second = await _scan(db, court, clients, notifier)
    assert second["rows_written"] == 0
    assert second["slack_sent"] == 1
    assert second["slack_pending"] == 0
    assert len(db.notifications.docs) == 1
    assert len(db.docket_events.docs) == 1
    assert db.docket_events.docs[0]["slack_delivered"] is True
    assert db.docket_events.docs[0]["alert_complete"] is True
    assert db.docket_events.docs[0]["slack_attempts"] == 2
    assert len(notifier.calls) == 2


@pytest.mark.asyncio
async def test_old_case_with_a_new_entry_is_not_dropped(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(dateFiled="2010-01-01", recap_documents=[])],
        brief={
            "id": 1001,
            "court_id": "flmd",
            "docket_number": "2:10-cr-00001",
            "case_name": "United States v. John Smith",
            "date_filed": "2010-01-01",
        },
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 80, "description": "Failure to appear", "date_filed": today}],
    )
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert any(call[0] == "brief" for call in court.calls)
    assert result["rows_written"] == 1
    assert db.docket_events.docs[0]["event_type"] == "failure_to_appear"
    window = court.calls[0]
    assert window[0] == "search_recent"
    assert "filed_after" not in window[1]
    assert window[1]["entry_date_filed_after"]

    stale = _CourtListener(
        hits=[_hit(entry_date_filed="2001-01-01", dateFiled="2001-01-01", recap_documents=[])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:01-cr-00001", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[],
    )
    skipped = await _scan(_DB(), stale, [_client()])
    assert skipped["rows_written"] == 0
    assert not any(call[0] == "brief" for call in stale.calls)


@pytest.mark.asyncio
async def test_recap_document_and_its_entry_alert_once(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[{
            "id": 9,
            "docket_entry_id": 77,
            "description": "Arrest warrant issued",
            "entry_date_filed": today,
        }])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[{
            "id": 77,
            "description": "Arrest warrant issued",
            "date_filed": today,
            "recap_documents": [{"id": 9}],
        }],
    )
    db = _DB()
    notifier = _Notifier()
    result = await _scan(db, court, [_client()], notifier)
    assert result["rows_written"] == 1
    assert result["slack_sent"] == 1
    assert len(db.notifications.docs) == 1
    assert db.docket_events.docs[0]["filing_id"] == "entry:77"
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_claim_is_atomic_and_indexed(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Capias issued"}])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[],
    )
    db = _DB()
    first = await _scan(db, court, [_client()])
    second = await _scan(db, court, [_client()])
    assert first["rows_written"] == 1
    assert second["rows_written"] == 0
    assert len(db.docket_events.docs) == 1
    assert len(db.notifications.docs) == 1
    index = db.docket_events.indexes[0]
    assert index[1]["unique"] is True
    assert index[1]["partialFilterExpression"] == {"source": "courtlistener_federal_bond_risk"}

    class _DuplicateKey(Exception):
        code = 11000

    class _LostClaim(_Collection):
        async def update_one(self, filt, update, upsert=False):
            if upsert:
                raise _DuplicateKey("E11000 duplicate key")
            return await super().update_one(filt, update, upsert=upsert)

    lost = _DB()
    lost.docket_events = _LostClaim()
    outcome = await _scan(lost, court, [_client()])
    assert outcome["rows_written"] == 0
    assert lost.notifications.docs == []
    assert lost.docket_events.docs == []


@pytest.mark.asyncio
async def test_slack_cap_does_not_mark_the_unsent_alert_complete(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr("dashboard.services.federal_bond_risk.MAX_SLACK", 1)
    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[_defendant(dob="01/15/1985")],
        entries=[
            {"id": 21, "description": "Detention hearing", "date_filed": today},
            {"id": 22, "description": "Failure to appear", "date_filed": today},
        ],
    )
    notifier = _Notifier()
    db = _DB()
    clients = [_client(dobs=["1985-01-15"])]
    first = await _scan(db, court, clients, notifier)
    assert first["rows_written"] == 2
    assert first["slack_sent"] == 1
    assert first["slack_pending"] == 1
    pending = [row for row in db.docket_events.docs if not row["alert_complete"]]
    assert len(pending) == 1
    assert pending[0]["slack_delivered"] is False
    second = await _scan(db, court, clients, notifier)
    assert second["rows_written"] == 0
    assert second["slack_sent"] == 1
    assert len(db.notifications.docs) == 2
    assert all(row["alert_complete"] and row["slack_delivered"] for row in db.docket_events.docs)


@pytest.mark.asyncio
async def test_loader_pages_every_arrest_before_judging_dob_conflict():
    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "status": "active",
        "defendant_name": "John Smith",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    db.active_bonds.docs.append({
        "_id": "bond-2",
        "Bond_Case_ID": "bond-2",
        "status": "active",
        "defendant_name": "Jane Roe",
        "Defendant_ID": "def-2",
        "date_of_birth": "1970-01-01",
    })
    for index in range(9):
        db.arrests.docs.append({
            "_id": f"a{index:02d}",
            "Defendant_ID": "def-1",
            "defendant_name": "John Smith",
            "date_of_birth": "1985-01-15",
        })
    db.arrests.docs.append({
        "_id": "z-conflict",
        "Defendant_ID": "def-2",
        "defendant_name": "Jane Roe",
        "date_of_birth": "1972-03-03",
    })
    clients = await load_active_clients(db, page_size=2)
    by_bond = {client.bond_case_id: client for client in clients}
    assert by_bond["bond-1"].dob_conflict is False
    assert by_bond["bond-2"].dob_conflict is True
    assert "1972-03-03" in by_bond["bond-2"].dobs


@pytest.mark.asyncio
async def test_every_search_source_failing_is_not_an_empty_success(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(search_errors={
        "r": {"success": False, "error": "HTTP 403", "status_code": 403, "results": []},
        "rd": {"success": False, "error": "HTTP 500", "status_code": 500, "results": []},
        "d": {"success": False, "error": "bad_json", "status_code": 200, "results": []},
    })
    db = _DB()
    result = await _scan(db, court, [_client()])
    assert result["success"] is False
    assert result["skipped"] == "search_failed"
    assert "403" in result["warning"]
    assert result["rows_written"] == 0
    assert db.docket_events.docs == []
    assert db.notifications.docs == []


@pytest.mark.asyncio
async def test_one_failed_search_source_does_not_hide_a_successful_one(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[{"name": "Smith, John"}],
        search_errors={"rd": {"success": False, "error": "HTTP 403", "status_code": 403, "results": []}},
    )
    db = _DB()
    result = await _scan(db, court, [_client()])
    assert result["success"] is True
    assert result["skipped"] is None
    assert result["rows_written"] == 1
    assert result["incomplete_coverage"] is True
    assert result["truncated"] is True
    assert "rd" in result["warning"]
    assert "403" in result["warning"]


def test_conflicting_client_dobs_do_not_auto_confirm():
    docket = _docket_for(DocketParty("Smith, John", frozenset({"1985-01-15"})))
    conflicted = ClientIdentity(
        bond_case_id="bond-1",
        defendant_id="def-1",
        names=("John Smith",),
        dobs=frozenset({"1985-01-15", "1990-02-02"}),
        numbers=frozenset(),
        status="active",
        dob_conflict=True,
    )
    assert match_client(conflicted, docket) == ("possible", ())
    agreed = ClientIdentity(
        bond_case_id="bond-1",
        defendant_id="def-1",
        names=("John Smith",),
        dobs=frozenset({"1985-01-15"}),
        numbers=frozenset(),
        status="active",
    )
    assert match_client(agreed, docket) == ("confirmed", ("dob",))


def test_dob_conflict_blocks_case_number_confirmation():
    docket = _docket_for(DocketParty("Smith, John", frozenset()))
    conflicted = ClientIdentity(
        bond_case_id="bond-1",
        defendant_id="def-1",
        names=("John Smith",),
        dobs=frozenset({"1985-01-15", "1990-02-02"}),
        numbers=frozenset({"2:24-cr-00123"}),
        status="active",
        dob_conflict=True,
    )
    assert match_client(conflicted, docket) == ("possible", ())
    agreed = ClientIdentity(
        bond_case_id="bond-1",
        defendant_id="def-1",
        names=("John Smith",),
        dobs=frozenset({"1985-01-15"}),
        numbers=frozenset({"2:24-cr-00123"}),
        status="active",
    )
    assert match_client(agreed, docket) == ("confirmed", ("case_number",))


@pytest.mark.asyncio
async def test_top_level_recap_document_is_classified(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[],
        rd_hits=[{
            "id": 55,
            "docket_id": 1001,
            "court_id": "flmd",
            "caseName": "United States v. John Smith",
            "docketNumber": "2:24-cr-00123",
            "description": "Failure to appear",
            "entry_date_filed": datetime.now(timezone.utc).date().isoformat(),
        }],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[{"name": "Smith, John"}],
        entries=[],
    )
    db = _DB()
    result = await _scan(db, court, [_client()])
    assert result["rows_written"] == 1
    assert db.docket_events.docs[0]["event_type"] == "failure_to_appear"
    assert db.docket_events.docs[0]["filing_id"] == "doc:55"


@pytest.mark.asyncio
async def test_parties_follow_the_next_page(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            if "cursor=parties2" in url:
                return _Response({"results": [{"id": 2, "name": "Roe, Jane"}], "next": None})
            if url == "/parties/":
                return _Response({
                    "results": [{"id": 1, "name": "Smith, John"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/parties/?cursor=parties2",
                })
            raise AssertionError(url)

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()
    found = await client.list_docket_parties(5, page_size=20, max_pages=50, pause_s=0)
    assert found["pages_fetched"] == 2
    assert [row["name"] for row in found["results"]] == ["Smith, John", "Roe, Jane"]
    assert calls[0][1]["fields"] == DOCKET_PARTY_FIELDS
    assert "party_types" in calls[0][1]["fields"]
    assert calls[0][1]["filter_nested_results"] == "true"
    assert calls[0][1]["page_size"] == 20


@pytest.mark.asyncio
async def test_abandoned_slack_claim_can_be_taken_again(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _claim_slack_delivery

    db = _DB()
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.docket_events.docs.append({
        "_id": "stale-claim",
        "source": SOURCE,
        "source_id": "fl-federal:1:warrant:entry:9:bond-1",
        "slack_delivered": False,
        "slack_claim": True,
        "slack_claim_at": stale,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for John Smith.",
        "court_name": "M.D. Florida",
        "docket_number": "2:24-cr-00123",
        "event_type": "warrant",
        "filing_id": "entry:9",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-1",
        "slack_attempts": 1,
    })
    assert await _claim_slack_delivery(
        db, "fl-federal:1:warrant:entry:9:bond-1", match_status="confirmed", revision=0,
    )
    db.docket_events.docs[0]["slack_claim"] = True
    db.docket_events.docs[0]["slack_claim_at"] = stale
    db.docket_events.docs[0]["slack_delivered"] = False
    court = _CourtListener(hits=[])
    notifier = _Notifier()
    result = await _scan(db, court, [_client()], notifier)
    assert result["slack_sent"] == 1
    assert db.docket_events.docs[0]["slack_delivered"] is True
    assert db.docket_events.docs[0]["alert_complete"] is True
    assert len(db.notifications.docs) == 0


def test_order_as_to_stops_at_the_party_name():
    from dashboard.services.federal_bond_risk import (
        _events_from_text,
        decide_matches,
        parties_affected_by_text,
        parties_named_in_filing,
    )

    text = "ORDER as to John Smith denying Motion to Modify Conditions of Release"
    assert parties_named_in_filing(text) == ("John Smith",)
    assert parties_affected_by_text(text, ["Smith, John", "Roe, Jane"]) == ("Smith, John",)
    # A capture that is not itself a party still falls back to the party named in it.
    assert parties_affected_by_text(
        "ORDER as to John Smith foobar",
        ["Smith, John", "Roe, Jane"],
    ) == ("Smith, John",)
    events = _events_from_text(text, "entry:77", ["Smith, John", "Roe, Jane"])
    assert events
    assert all(event.affected_names == ("Smith, John",) for event in events)
    docket = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=events,
    )
    clients = [
        _client(name="John Smith", dobs=["1985-01-15"], bond="bond-j", defendant="def-j"),
        _client(name="Jane Roe", dobs=["1990-02-02"], bond="bond-r", defendant="def-r"),
    ]
    decisions = decide_matches(clients, docket)
    assert decisions
    assert {row.client.bond_case_id for row in decisions} == {"bond-j"}


@pytest.mark.asyncio
async def test_hydration_past_the_ceiling_continues_on_the_next_scan(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr("dashboard.services.federal_bond_risk.HYDRATE_CEILING", 1)
    hits = [
        _hit(id=1001, docket_id=1001),
        _hit(id=1002, docket_id=1002, docketNumber="2:24-cr-00456"),
    ]
    court = _CourtListener(hits=hits, parties=[{"name": "Smith, John"}])
    db = _DB()
    first = await _scan(db, court, [_client()])
    assert first["truncated"] is True
    assert first["rows_written"] == 1
    assert "next scan" in first["warning"]
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002"}


@pytest.mark.asyncio
async def test_failed_notification_is_written_on_the_next_scan(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    real_update = db.notifications.update_one
    state = {"calls": 0}

    async def fail_once(filt, update, upsert=False):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("notification down")
        return await real_update(filt, update, upsert=upsert)

    db.notifications.update_one = fail_once
    first = await _scan(db, court, [_client()])
    assert first["rows_written"] == 1
    assert db.notifications.docs == []
    assert db.docket_events.docs[0].get("notification_written") is not True
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 0
    assert len(db.docket_events.docs) == 1
    assert len(db.notifications.docs) == 1
    assert db.notifications.docs[0]["metadata"]["source_id"] == db.docket_events.docs[0]["source_id"]
    assert db.docket_events.docs[0]["notification_written"] is True


def test_conflicting_party_dobs_do_not_auto_confirm():
    docket = _docket_for(DocketParty("Smith, John", frozenset({"1985-01-15", "1990-02-02"})))
    client = _client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])
    assert match_client(client, docket) == ("possible", ())
    agreed = _docket_for(DocketParty("Smith, John", frozenset({"1985-01-15"})))
    assert match_client(client, agreed)[0] == "confirmed"


@pytest.mark.asyncio
async def test_seed_cursor_id_is_unique_per_tenant(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    from dashboard.services.federal_bond_risk import _save_seed_offset, _seed_offset
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedDatabase

    class _Ids:
        def __init__(self):
            self.docs = []

        async def find_one(self, filt=None):
            for doc in self.docs:
                if all(doc.get(key) == value for key, value in (filt or {}).items()):
                    return dict(doc)
            return None

        async def update_one(self, filt, update, upsert=False):
            for doc in self.docs:
                if all(doc.get(key) == value for key, value in (filt or {}).items()):
                    doc.update((update or {}).get("$set") or {})
                    return type("R", (), {"matched_count": 1, "upserted_id": None})()
            if not upsert:
                return type("R", (), {"matched_count": 0, "upserted_id": None})()
            new_id = (filt or {}).get("_id")
            if any(doc.get("_id") == new_id for doc in self.docs):
                raise RuntimeError("E11000 duplicate key")
            stored = dict(filt or {})
            stored.update((update or {}).get("$set") or {})
            self.docs.append(stored)
            return type("R", (), {"matched_count": 0, "upserted_id": new_id})()

    class _Raw:
        def __init__(self):
            self.federal_bond_risk_state = _Ids()

    raw = _Raw()
    db = TenantScopedDatabase(raw)
    with bind_job_tenant("alpha", "federal_bond_risk"):
        await _save_seed_offset(db, 3)
    with bind_job_tenant("beta", "federal_bond_risk"):
        await _save_seed_offset(db, 7)
    with bind_job_tenant("alpha", "federal_bond_risk"):
        assert await _seed_offset(db) == 3
    with bind_job_tenant("beta", "federal_bond_risk"):
        assert await _seed_offset(db) == 7
    assert {doc["_id"] for doc in raw.federal_bond_risk_state.docs} == {
        "seed_cursor:alpha",
        "seed_cursor:beta",
    }


@pytest.mark.asyncio
async def test_party_list_outage_is_not_an_empty_success(monkeypatch):
    _enable(monkeypatch)
    outage = {"success": False, "error": "HTTP 403", "status_code": 403, "results": []}
    court = _CourtListener(hits=[_hit()], parties_error=outage)
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["success"] is False
    assert result["skipped"] == "party_hydration_failed"
    assert "403" in result["warning"]
    assert result["rows_written"] == 0
    assert db.docket_events.docs == []

    partial = _CourtListener(
        hits=[
            _hit(id=1001, docket_id=1001),
            _hit(id=1002, docket_id=1002, docketNumber="2:24-cr-00456"),
        ],
        parties=[_defendant(dob="01/15/1985")],
        parties_error={"success": False, "error": "bad_json", "status_code": 200, "results": []},
        parties_error_ids={1002},
    )
    partial_db = _DB()
    kept = await _scan(partial_db, partial, [_client(dobs=["1985-01-15"])])
    assert kept["success"] is True
    assert kept["rows_written"] == 1
    assert "1002" in kept["warning"]
    assert partial_db.docket_events.docs[0]["docket_id"] == "1001"


@pytest.mark.asyncio
async def test_later_conflicting_party_dobs_downgrade_a_confirmed_link(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    db = _DB()
    client = _client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])
    first = await _scan(db, court, [client])
    assert first["confirmed"] == 1
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert db.docket_events.docs[0]["bond_case_id"] == "bond-1"

    court.parties = [{
        "name": "Smith, John",
        "party_types": [
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 02/02/1990"},
        ],
    }]
    second = await _scan(db, court, [client])
    assert second["rows_written"] == 1
    row = db.docket_events.docs[0]
    assert len(db.docket_events.docs) == 1
    assert row["match_status"] == "possible"
    assert row["auto_linked"] is False
    assert row["bond_case_id"] == ""
    assert row["matched_defendant_id"] is None
    assert row["downgraded_from"] == "confirmed"
    assert row["downgrade_reason"] == "identity_no_longer_confirms"
    assert row["prior_bond_case_id"] == "bond-1"
    assert row["match_history"][-1]["from"] == "confirmed"
    assert row["match_history"][-1]["to"] == "possible"
    assert any(note["metadata"]["match_status"] == "possible" for note in db.notifications.docs)
    assert any(note["metadata"].get("downgraded_from") == "confirmed" for note in db.notifications.docs)

    court.parties = [{"name": "Smith, John", "extra_info": "DOB: 02/02/1990"}]
    await _scan(db, court, [client])
    withdrawn = db.docket_events.docs[0]
    assert withdrawn["match_status"] == "withdrawn"
    assert withdrawn["auto_linked"] is False
    assert withdrawn["downgrade_reason"] == "identity_no_longer_matches"
    assert withdrawn["match_history"][-1]["to"] == "withdrawn"
    assert withdrawn["_id"]


@pytest.mark.asyncio
async def test_failed_slack_retry_uses_the_downgraded_wording(monkeypatch):
    _enable(monkeypatch)

    class _FailThenSend:
        def __init__(self):
            self.calls = []

        def notify_alert(self, **kwargs):
            self.calls.append(kwargs)
            return len(self.calls) > 1

    court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    db = _DB()
    notifier = _FailThenSend()
    client = _client(dobs=["1985-01-15"])
    first = await _scan(db, court, [client], notifier)
    assert first["slack_sent"] == 0
    assert notifier.calls[0]["details"]["auto_linked"] is True
    assert db.docket_events.docs[0]["slack_delivered"] is False

    court.parties = [{
        "name": "Smith, John",
        "party_types": [
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 02/02/1990"},
        ],
    }]
    second = await _scan(db, court, [client], notifier)
    assert second["slack_sent"] == 1
    assert notifier.calls[-1]["details"]["auto_linked"] is False
    assert "no longer confirms" in notifier.calls[-1]["message"]
    assert all(not call["details"]["auto_linked"] for call in notifier.calls[1:])


@pytest.mark.asyncio
async def test_inactive_client_is_not_retried_as_confirmed(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, run_federal_bond_risk_scan

    db = _DB()
    db.docket_events.docs.append({
        "_id": "old-link",
        "source": SOURCE,
        "source_id": "fl-federal:9:warrant:entry:1:bond-missing",
        "slack_delivered": False,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for Pat Sample.",
        "court_name": "M.D. Florida",
        "docket_number": "9:20-cr-00001",
        "event_type": "warrant",
        "filing_id": "entry:1",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-missing",
        "slack_attempts": 1,
    })
    notifier = _Notifier()
    await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=notifier, pause_s=0)
    assert db.docket_events.docs[0]["match_status"] == "withdrawn"
    assert db.docket_events.docs[0]["auto_linked"] is False
    assert notifier.calls[0]["details"]["auto_linked"] is False
    assert "no longer confirms" in notifier.calls[0]["message"]


@pytest.mark.asyncio
async def test_et_al_caption_still_matches_a_loaded_codefendant(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit(
            caseName="United States v. Lead Defendant et al.",
            recap_documents=[{
                "id": 9,
                "description": "Order Setting Conditions of Release as to John Smith",
            }],
        )],
        parties=[
            {"name": "Defendant, Lead"},
            _defendant(dob="01/15/1985"),
        ],
    )
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert result["rows_written"] == 1
    assert result["confirmed"] == 1
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert db.docket_events.docs[0]["defendant_name"] == "John Smith"


@pytest.mark.asyncio
async def test_notification_claim_is_one_row_under_a_duplicate_key(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _ensure_notification

    db = _DB()
    db.docket_events.docs.append({
        "source": SOURCE,
        "source_id": "src-1",
        "slack_delivered": False,
    })
    note = {"title": "Federal bond risk", "metadata": {"source_id": "src-1", "match_status": "possible"}}
    assert await _ensure_notification(db, note, "src-1", "possible") is True
    assert await _ensure_notification(db, note, "src-1", "possible") is True
    assert len(db.notifications.docs) == 1
    assert db.docket_events.docs[0]["notification_written"] is True

    class _DuplicateKey(Exception):
        code = 11000

    class _Racer(_Collection):
        async def update_one(self, filt, update, upsert=False):
            if upsert:
                raise _DuplicateKey("E11000 duplicate key")
            return await super().update_one(filt, update, upsert=upsert)

    raced = _DB()
    raced.notifications = _Racer()
    raced.docket_events.docs.append({"source": SOURCE, "source_id": "src-2"})
    assert await _ensure_notification(raced, note, "src-2", "possible") is True
    assert raced.notifications.docs == []
    assert raced.docket_events.docs[0]["notification_written"] is True


class _FailNotifier:
    def __init__(self):
        self.calls = []

    def notify_alert(self, **kwargs):
        self.calls.append(kwargs)
        return False


@pytest.mark.asyncio
async def test_failed_slack_attempts_count_against_the_cap(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr("dashboard.services.federal_bond_risk.MAX_SLACK", 1)
    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        brief={"id": 1001, "court_id": "flmd", "docket_number": "2:24-cr-00123", "case_name": "United States v. John Smith"},
        parties=[_defendant(dob="01/15/1985")],
        entries=[
            {"id": 21, "description": "Detention hearing", "date_filed": today},
            {"id": 22, "description": "Failure to appear", "date_filed": today},
        ],
    )
    notifier = _FailNotifier()
    db = _DB()
    result = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier)
    assert result["slack_sent"] == 0
    assert result["rows_written"] == 2
    assert len(notifier.calls) == 1
    assert sum(1 for row in db.docket_events.docs if row.get("slack_attempts")) == 1

    pending_db = _DB()
    from dashboard.services.federal_bond_risk import SOURCE
    for source_id, event in (("pending-a", "warrant"), ("pending-b", "failure_to_appear")):
        pending_db.docket_events.docs.append({
            "_id": source_id,
            "source": SOURCE,
            "source_id": source_id,
            "slack_delivered": False,
            "slack_attempts": 0,
            "title": "Federal bond risk",
            "description": "Needs a retry.",
            "court_name": "M.D. Florida",
            "docket_number": "2:24-cr-00123",
            "event_type": event,
            "filing_id": source_id,
            "match_status": "possible",
            "auto_linked": False,
        })
    pending_notifier = _FailNotifier()
    pending = await _scan(pending_db, _CourtListener(hits=[]), [_client()], pending_notifier)
    assert pending["slack_sent"] == 0
    assert len(pending_notifier.calls) == 1
    attempted = [row for row in pending_db.docket_events.docs if row.get("slack_attempts")]
    assert len(attempted) == 1


@pytest.mark.asyncio
async def test_missing_unique_index_aborts_before_any_alert(monkeypatch):
    _enable(monkeypatch)

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("index down")

    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    db.docket_events.create_index = unavailable
    result = await _scan(db, court, [_client()])
    assert result["success"] is False
    assert result["skipped"] == "index_unavailable"
    assert result["rows_written"] == 0
    assert db.docket_events.docs == []
    assert db.notifications.docs == []
    assert court.calls == []


def test_filing_as_to_one_defendant_does_not_link_the_other():
    from dashboard.services.federal_bond_risk import decide_matches

    docket = _docket_for(
        DocketParty("Smith, John", frozenset({"1985-01-15"})),
        DocketParty("Roe, Jane", frozenset({"1990-02-02"})),
        events=[DocketEvent("warrant", "Warrant", "entry:90", ("Jane Roe",))],
    )
    clients = [
        _client(name="John Smith", dobs=["1985-01-15"], bond="bond-j", defendant="def-j"),
        _client(name="Jane Roe", dobs=["1990-02-02"], bond="bond-r", defendant="def-r"),
    ]
    decisions = decide_matches(clients, docket)
    assert [row.client.bond_case_id for row in decisions] == ["bond-r"]
    assert decisions[0].status == "confirmed"


@pytest.mark.asyncio
async def test_active_bonds_page_past_the_first_batch():
    db = _DB()
    for index in range(201):
        db.active_bonds.docs.append({
            "_id": f"{index:04d}",
            "status": "active",
            "Bond_Case_ID": f"bond-{index:04d}",
            "defendant_name": "Last Person" if index == 200 else "Other Person",
        })
    clients = await load_active_clients(db, page_size=200)
    assert len(clients) == 201
    assert clients[-1].bond_case_id == "bond-0200"


def _window_hit(docket_id: int, day: str, description: str, number: str = "2:24-cr-00123"):
    return _hit(
        id=docket_id,
        docket_id=docket_id,
        docketNumber=number,
        entry_date_filed=day,
        recap_documents=[{"id": docket_id, "description": description, "entry_date_filed": day}],
    )


class _PagedSearch(_CourtListener):
    def __init__(self):
        super().__init__(parties=[{"name": "Smith, John"}])

    async def search_recent(self, **kwargs):
        self.calls.append(("search_recent", kwargs))
        if kwargs["search_type"] != "r":
            return {"success": True, "results": [], "truncated": False}
        before = kwargs.get("entry_date_filed_before")
        if not before:
            return {
                "success": True,
                "truncated": True,
                "results": [_window_hit(1001, "2026-11-20", "Arrest warrant issued")],
            }
        if before == "2026-11-20":
            return {
                "success": True,
                "truncated": False,
                "results": [_window_hit(1002, "2026-11-01", "Failure to appear", "2:24-cr-00456")],
            }
        return {"success": True, "results": [], "truncated": False}


class _StuckSearch(_CourtListener):
    def __init__(self):
        super().__init__(parties=[{"name": "Smith, John"}])

    async def search_recent(self, **kwargs):
        self.calls.append(("search_recent", kwargs))
        if kwargs["search_type"] != "r":
            return {"success": True, "results": [], "truncated": False}
        return {
            "success": True,
            "truncated": True,
            "results": [_window_hit(1001, "2026-11-20", "Arrest warrant issued")],
        }


@pytest.mark.asyncio
async def test_failed_scan_is_not_logged_as_a_completed_skip(monkeypatch):
    from dashboard.cron import _run_federal_bond_risk

    async def failed(_db):
        return {"success": False, "skipped": "search_failed", "warning": "CourtListener search failed"}

    async def quiet(_db):
        return {"success": True, "skipped": "flag_off"}

    monkeypatch.setattr("dashboard.extensions.get_db", lambda: object())
    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", failed)
    with pytest.raises(RuntimeError, match="search_failed"):
        await _run_federal_bond_risk()
    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", quiet)
    await _run_federal_bond_risk()


class _SameDayResume(_CourtListener):
    def __init__(self):
        super().__init__(parties=[{"name": "Smith, John"}])

    async def search_recent(self, **kwargs):
        self.calls.append(("search_recent", kwargs))
        if kwargs["search_type"] != "r":
            return {"success": True, "results": [], "truncated": False}
        if not kwargs.get("start_url"):
            return {
                "success": True,
                "truncated": True,
                "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=same-day",
                "results": [_window_hit(1001, "2026-11-20", "Order Setting Conditions of Release")],
            }
        assert kwargs["start_url"].endswith("cursor=same-day")
        return {
            "success": True,
            "truncated": False,
            "results": [_window_hit(1002, "2026-11-20", "Order Setting Conditions of Release", "2:24-cr-00456")],
        }


@pytest.mark.asyncio
async def test_same_day_search_ceiling_resumes_from_the_cursor(monkeypatch):
    _enable(monkeypatch)
    court = _SameDayResume()
    db = _DB()
    first = await _scan(db, court, [_client()])
    assert first["rows_written"] == 1
    assert db.docket_events.docs[0]["docket_id"] == "1001"
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002"}
    assert second["success"] is True


@pytest.mark.asyncio
async def test_clearing_a_pending_audit_does_not_erase_a_newer_one():
    from dashboard.services.federal_bond_risk import SOURCE, _clear_pending_audit

    db = _DB()
    db.docket_events.docs.append({
        "_id": "row",
        "source": SOURCE,
        "source_id": "src-1",
        "audit_pending": [
            {"event_id": "old", "reason": "match_recorded"},
            {"event_id": "new", "reason": "withdrawn_restored"},
        ],
    })
    real_find = db.docket_events.find_one

    async def stale_find(filt=None, projection=None, sort=None):
        row = await real_find(filt, projection, sort)
        if not row or "audit_pending" not in row:
            return row
        stale = dict(row)
        stale["audit_pending"] = [item for item in row["audit_pending"] if item["event_id"] != "new"]
        return stale

    db.docket_events.find_one = stale_find
    await _clear_pending_audit(db, "src-1", "old")
    assert [item["event_id"] for item in db.docket_events.docs[0]["audit_pending"]] == ["new"]


@pytest.mark.asyncio
async def test_entry_pages_cover_the_lookback_window(monkeypatch):
    _enable(monkeypatch)
    calls = []

    class _Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            page = len(calls)
            if page == 1:
                return _Response({
                    "results": [{"id": 1, "description": "Status conference", "date_filed": "2026-11-20"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=e2",
                })
            if page == 2:
                return _Response({
                    "results": [{"id": 2, "description": "Arrest warrant issued", "date_filed": "2026-11-05"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=e3",
                })
            if page == 3:
                return _Response({
                    "results": [{"id": 3, "description": "Ancient minute", "date_filed": "2020-01-01"}],
                    "next": "https://www.courtlistener.com/api/rest/v4/docket-entries/?cursor=e4",
                })
            raise AssertionError(url)

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()
    found = await client.list_docket_entries(
        5, page_size=20, max_pages=MAX_ENTRY_PAGES, pause_s=0, until_date="2026-10-09",
    )
    assert found["truncated"] is False
    assert found["pages_fetched"] == 3
    assert [row["id"] for row in found["results"]] == [1, 2, 3]
    assert len(calls) == 3

    class _Deep(_CourtListener):
        async def list_docket_entries(self, docket_id, **kwargs):
            self.calls.append(("entries", kwargs))
            assert int(kwargs["max_pages"]) > 2
            assert kwargs.get("until_date")
            today = datetime.now(timezone.utc).date().isoformat()
            return {
                "success": True,
                "truncated": False,
                "results": [
                    {"id": 1, "description": "Status conference", "date_filed": today},
                    {"id": 80, "description": "Arrest warrant issued", "date_filed": today},
                ],
            }

    deep = _Deep(
        hits=[_hit(recap_documents=[])],
        parties=[{"name": "Smith, John"}],
    )
    db = _DB()
    result = await _scan(db, deep, [_client()])
    assert result["rows_written"] == 1
    assert db.docket_events.docs[0]["event_type"] == "warrant"
    assert db.docket_events.docs[0]["filing_id"] == "entry:80"


@pytest.mark.asyncio
async def test_truncated_search_continues_before_the_oldest_hit(monkeypatch):
    _enable(monkeypatch)
    court = _PagedSearch()
    db = _DB()
    first = await _scan(db, court, [_client()])
    assert first["rows_written"] == 1
    assert first["truncated"] is True
    assert "1,000-hit ceiling" in first["warning"]
    assert db.docket_events.docs[0]["docket_id"] == "1001"
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002"}
    r_calls = [kwargs for kind, kwargs in court.calls if kind == "search_recent" and kwargs["search_type"] == "r"]
    assert r_calls[0].get("entry_date_filed_before") in (None, "")
    assert r_calls[1]["entry_date_filed_before"] == "2026-11-20"

    stuck = _StuckSearch()
    stuck_db = _DB()
    opened = await _scan(stuck_db, stuck, [_client()])
    assert opened["rows_written"] == 1
    blocked = await _scan(stuck_db, stuck, [_client()])
    assert blocked["success"] is False
    assert blocked["skipped"] == "search_truncated"
    assert blocked["rows_written"] == 0
    assert len(stuck_db.docket_events.docs) == 1


@pytest.mark.asyncio
async def test_truncated_party_list_does_not_match_and_entry_truncation_is_named(monkeypatch):
    _enable(monkeypatch)
    parties = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John", "extra_info": "DOB: 01/15/1985"}])
    parties.parties_truncated = True
    missed = await _scan(_DB(), parties, [_client(dobs=["1985-01-15"])])
    assert missed["success"] is False
    assert missed["skipped"] == "party_hydration_failed"
    assert missed["rows_written"] == 0

    entries = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued"}])],
        parties=[{"name": "Smith, John"}],
        entries=[{"id": 21, "description": "Detention hearing", "date_filed": datetime.now(timezone.utc).date().isoformat()}],
    )
    entries.entries_truncated = True
    db = _DB()
    kept = await _scan(db, entries, [_client()])
    assert kept["success"] is False
    assert kept["skipped"] == "entry_hydration_failed"
    assert kept["rows_written"] == 0
    assert db.docket_events.docs == []


@pytest.mark.asyncio
async def test_audit_event_id_is_claimed_once(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import (
        AUDIT_INDEX,
        SOURCE,
        _insert_audit,
        ensure_federal_bond_alert_index,
    )

    db = _DB()
    await ensure_federal_bond_alert_index(db)
    assert any(
        kwargs.get("name") == AUDIT_INDEX and kwargs.get("unique") is True
        for _keys, kwargs in db.audit_events.indexes
    )
    payload = {
        "event_id": "evt-1",
        "source": SOURCE,
        "reason": "match_recorded",
        "actor": "system",
        "event_type": "federal_bond_risk_match_changed",
    }
    assert await _insert_audit(db, payload) is True
    assert await _insert_audit(db, dict(payload)) is True
    assert len(db.audit_events.docs) == 1
    assert db.audit_events.docs[0]["event_id"] == "evt-1"

    class _DuplicateKey(Exception):
        code = 11000

    class _Racer(_Collection):
        async def update_one(self, filt, update, upsert=False):
            if upsert:
                raise _DuplicateKey("E11000 duplicate key")
            return await super().update_one(filt, update, upsert=upsert)

    raced = _DB()
    raced.audit_events = _Racer()
    raced.audit_events.docs.append(dict(payload))
    assert await _insert_audit(raced, payload) is True
    assert len(raced.audit_events.docs) == 1


@pytest.mark.asyncio
async def test_search_pages_through_the_filing_window(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            calls.append(url)
            page = len(calls) - 1
            if page >= 5:
                raise AssertionError("paged past the filing window")
            filed = "2026-11-18" if page < 4 else "2001-01-01"
            nxt = "stop" if page >= 4 else f"p{page + 1}"
            return _Response({
                "results": [{"id": page + 1, "entry_date_filed": filed}],
                "next": f"https://www.courtlistener.com/api/rest/v4/search/?cursor={nxt}",
            })

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()
    found = await client.search_recent(
        search_type="rd",
        query="warrant",
        courts=["flmd"],
        entry_date_filed_after="2026-10-06",
        until_entry_before="2026-10-06",
        page_size=20,
        max_pages=50,
        pause_s=0,
    )
    assert found["pages_fetched"] == 5
    assert found["truncated"] is False
    assert len(calls) == 5
    assert not any("cursor=stop" in url for url in calls)


@pytest.mark.asyncio
async def test_pending_slack_retries_outside_the_search_and_claims_once(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _claim_slack_delivery

    db = _DB()
    db.docket_events.docs.append({
        "_id": "pending-1",
        "source": SOURCE,
        "source_id": "fl-federal:9:warrant:entry:1:bond-9",
        "slack_delivered": False,
        "alert_complete": False,
        "slack_claim": False,
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for Pat Sample.",
        "court_name": "M.D. Florida",
        "docket_number": "9:20-cr-00001",
        "event_type": "warrant",
        "filing_id": "entry:1",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-9",
        "slack_attempts": 1,
    })
    court = _CourtListener(hits=[])
    notifier = _Notifier()
    result = await _scan(db, court, [_client()], notifier)
    assert result["rows_written"] == 0
    assert result["slack_sent"] == 1
    assert len(db.notifications.docs) == 0
    assert len(notifier.calls) == 1
    assert db.docket_events.docs[0]["alert_complete"] is True
    assert db.docket_events.docs[0]["slack_delivered"] is True

    db.docket_events.docs.append({
        "_id": "pending-2",
        "source": SOURCE,
        "source_id": "fl-federal:9:warrant:entry:2:bond-9",
        "slack_delivered": False,
        "slack_claim": False,
        "match_status": "confirmed",
    })
    assert await _claim_slack_delivery(
        db, "fl-federal:9:warrant:entry:2:bond-9", match_status="confirmed", revision=0,
    )
    assert not await _claim_slack_delivery(
        db, "fl-federal:9:warrant:entry:2:bond-9", match_status="confirmed", revision=0,
    )


def test_name_in_text_requires_contiguous_ordered_name():
    from dashboard.services.federal_bond_risk import (
        _events_from_text,
        _name_in_text,
        decide_matches,
        parties_affected_by_text,
    )

    assert _name_in_text("John Smith", "Arrest warrant issued for John Doe; Jane Smith") is False
    assert _name_in_text("John Smith", "Arrest warrant issued for John Doe, Jane Smith") is False
    assert _name_in_text("John Smith", "Order as to Doe, John; Smith, Jane") is False
    assert _name_in_text("John Smith", "Order as to Doe, John, Smith, Jane") is False
    assert _name_in_text("John Smith", "United States v. John Smith") is True
    assert _name_in_text("John Smith", "John v. Smith") is False
    assert _name_in_text("John Smith", "John and Smith") is False
    assert _name_in_text("John Smith", "Arrest warrant for John. Smith appeared.") is False
    assert _name_in_text("John Smith", "Arrest warrant for John Smith") is True
    assert _name_in_text("John Smith", "Arrest warrant for John A. Smith") is True
    assert _name_in_text("John Smith", "Arrest warrant for Smith, John") is True
    assert _name_in_text("John A. Smith", "Arrest warrant for Smith, John B.") is False
    assert _name_in_text("John A. Smith", "Arrest warrant for Smith, John A.") is True
    assert _name_in_text("John De La Cruz", "Arrest warrant for De La Cruz, John") is True
    assert _name_in_text("John De La Cruz", "Arrest warrant for John De La Cruz") is True
    assert _name_in_text("John De La Cruz", "John Doe; Jane De La Cruz") is False
    assert _name_in_text("John Smith", "Arrest warrant issued for Jane Smith, John Doe") is False
    assert _name_in_text("John Smith", "Jane Smith, John") is False
    assert _name_in_text("John Smith", "Smith, John Doe") is False
    assert _name_in_text("John Smith", "Arrest warrant as to Smith, John") is True
    assert _name_in_text("John Smith", "ORDER as to Smith, John denying Motion") is True
    assert _name_in_text("John De La Cruz", "Arrest warrant as to De La Cruz, John") is True
    assert _name_in_text("John De La Cruz", "Jane De La Cruz, John Doe") is False

    # Multi-party docket where only two *other* defendants' tokens overlap John Smith.
    text = "Arrest warrant issued for John Doe; Jane Smith"
    assert parties_affected_by_text(text, ["Smith, John", "Doe, John", "Smith, Jane"]) == (
        "Doe, John",
        "Smith, Jane",
    )
    # Single-defendant docket with an unrelated cross-token mention stays possible, not confirmed.
    events = _events_from_text(text, "entry:99", ["Smith, John"])
    assert events and events[0].affected_names == ()
    decisions = decide_matches(
        [_client(name="John Smith", dobs=["1985-01-15"])],
        _docket_for(DocketParty("Smith, John", frozenset({"1985-01-15"})), events=events),
    )
    assert decisions[0].status == "possible"
    assert decisions[0].confirmations == ()


@pytest.mark.asyncio
async def test_truncated_search_holds_checkpoint_until_capped_rows_are_stored(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr("dashboard.services.federal_bond_risk.MAX_ROWS", 2)

    class _SliceSearch(_CourtListener):
        def __init__(self):
            super().__init__(parties=[{"name": "Smith, John"}])
            self.r_starts = []

        async def search_recent(self, **kwargs):
            self.calls.append(("search_recent", kwargs))
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            start_url = kwargs.get("start_url") or ""
            self.r_starts.append(start_url)
            if not start_url:
                if len(self.r_starts) == 1:
                    return {
                        "success": True,
                        "truncated": True,
                        "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=slice2",
                        "results": [
                            _window_hit(1001, "2026-11-20", "Detention hearing", "2:24-cr-00101"),
                            _window_hit(1002, "2026-11-20", "Detention hearing", "2:24-cr-00102"),
                            _window_hit(1003, "2026-11-20", "Detention hearing", "2:24-cr-00103"),
                        ],
                    }
                # Newer filings arriving before Scan 2 would push 1003 off a re-queried slice.
                return {
                    "success": True,
                    "truncated": True,
                    "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=newer",
                    "results": [
                        _window_hit(9001, "2026-11-21", "Detention hearing", "2:24-cr-00901"),
                    ],
                }
            assert start_url.endswith("cursor=slice2")
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(1004, "2026-11-19", "Detention hearing", "2:24-cr-00104"),
                ],
            }

    court = _SliceSearch()
    db = _DB()

    first = await _scan(db, court, [_client()])
    assert first["rows_written"] == 2
    assert first["truncated"] is True
    assert "row cap" in first["warning"]
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002"}

    # Slice 1's remaining seed 1003 drains from durable state without re-searching CourtListener.
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002", "1003"}
    assert court.r_starts == [""]

    # Now slice 1 is completely drained, so Scan 3 advances to cursor=slice2.
    third = await _scan(db, court, [_client()])
    assert third["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"1001", "1002", "1003", "1004"}
    assert court.r_starts[-1].endswith("cursor=slice2")


@pytest.mark.asyncio
async def test_truncated_search_holds_checkpoint_while_hydration_batch_remains(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr("dashboard.services.federal_bond_risk.HYDRATE_CEILING", 1)

    class _PagedHydration(_CourtListener):
        def __init__(self):
            super().__init__(parties=[{"name": "Smith, John"}])
            self.r_starts = []

        async def search_recent(self, **kwargs):
            self.calls.append(("search_recent", kwargs))
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            start_url = kwargs.get("start_url") or ""
            self.r_starts.append(start_url)
            if not start_url:
                return {
                    "success": True,
                    "truncated": True,
                    "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=slice2",
                    "results": [
                        _window_hit(2001, "2026-11-20", "Detention hearing", "2:24-cr-00201"),
                        _window_hit(2002, "2026-11-20", "Detention hearing", "2:24-cr-00202"),
                    ],
                }
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(2003, "2026-11-19", "Detention hearing", "2:24-cr-00203"),
                ],
            }

    court = _PagedHydration()
    db = _DB()

    s1 = await _scan(db, court, [_client()])
    assert s1["rows_written"] == 1
    s2 = await _scan(db, court, [_client()])
    assert s2["rows_written"] == 1
    assert court.r_starts == [""]
    assert {row["docket_id"] for row in db.docket_events.docs} == {"2001", "2002"}

    s3 = await _scan(db, court, [_client()])
    assert s3["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"2001", "2002", "2003"}
    assert court.r_starts[-1].endswith("cursor=slice2")


@pytest.mark.asyncio
async def test_unauthorized_and_rate_limit_scans_raise_from_cron(monkeypatch):
    from dashboard.cron import _run_federal_bond_risk

    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")

    class _Blocked:
        def __init__(self, res):
            self.res = res

        async def search_recent(self, **_kwargs):
            return self.res

        async def close(self):
            return None

    async def _run_with(res):
        db = _DB()
        monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)
        monkeypatch.setattr(
            "dashboard.services.federal_bond_risk.load_active_clients",
            lambda _db: _async_val([_client()]),
        )
        monkeypatch.setattr(
            "dashboard.services.federal_bond_risk._unlink_inactive_clients",
            lambda _db, _clients: _async_val(None),
        )
        monkeypatch.setattr(
            "dashboard.services.courtlistener_client.CourtListenerClient",
            lambda api_token=None: _Blocked(res),
        )
        await _run_federal_bond_risk()

    async def _async_val(val):
        return val

    with pytest.raises(RuntimeError, match="unauthorized"):
        await _run_with({"success": False, "status_code": 401, "error": "unauthorized", "results": []})

    with pytest.raises(RuntimeError, match="rate_limited"):
        await _run_with({"success": False, "error": "ReadTimeout", "results": []})


@pytest.mark.asyncio
async def test_failed_withdrawal_notification_is_retried_on_next_scan(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")
    from dashboard.services.federal_bond_risk import SOURCE

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
        parties=[_defendant(dob="01/15/1985")],
    )
    first = await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    assert first["confirmed"] == 1
    assert len(db.notifications.docs) == 1

    # Bond closes -> _unlink_inactive_clients withdraws the row, and notification write fails once.
    db.active_bonds.docs[0]["status"] = "exonerated"
    real_update = db.notifications.update_one

    async def failing_update(filt, update, upsert=False):
        raise RuntimeError("transient notification failure")

    db.notifications.update_one = failing_update
    closed = await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert closed["skipped"] == "no_active_clients"
    assert db.docket_events.docs[0]["match_status"] == "withdrawn"
    assert db.docket_events.docs[0]["notification_written"] is False
    assert len(db.notifications.docs) == 1

    # Next scan restores notifications.update_one and retries the withdrawn notification once.
    db.notifications.update_one = real_update
    resumed = await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert resumed["skipped"] == "no_active_clients"
    assert db.docket_events.docs[0]["notification_written"] is True
    assert db.docket_events.docs[0]["notification_status"] == "withdrawn"
    assert len(db.notifications.docs) == 2
    assert db.notifications.docs[-1]["metadata"]["match_status"] == "withdrawn"
    assert db.docket_events.docs[0]["source"] == SOURCE

    # Repeating again is a no-op (still 2 notifications total).
    again = await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert again["skipped"] == "no_active_clients"
    assert len(db.notifications.docs) == 2


@pytest.mark.asyncio
async def test_corrected_filing_without_risk_phrase_withdraws_stale_link(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client(name="John Smith", dobs=["1985-01-15"])

    class _CorrectedCourt(_CourtListener):
        def __init__(self):
            super().__init__(
                hits=[_hit(recap_documents=[])],
                parties=[_defendant(dob="01/15/1985")],
            )
            self.stage = 1

        async def list_docket_entries(self, docket_id, **kwargs):
            self.calls.append(("entries", kwargs))
            if self.stage == 1:
                rows = [
                    {"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": today},
                    {"id": 99, "description": "Detention hearing", "date_filed": today},
                ]
            elif self.stage == 2:
                rows = [
                    {"id": 42, "description": "Status conference", "date_filed": today},
                    {"id": 99, "description": "Detention hearing", "date_filed": today},
                ]
            else:
                rows = [
                    {"id": 42, "description": "Status conference", "date_filed": today},
                    {"id": 99, "description": "Status conference", "date_filed": today},
                ]
            return {"success": True, "truncated": False, "results": rows}

    court = _CorrectedCourt()
    db = _DB()
    notifier = _Notifier()

    scan1 = await _scan(db, court, [client], notifier)
    assert scan1["confirmed"] == 2
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:42"]["auto_linked"] is True
    assert by_filing["entry:99"]["auto_linked"] is True

    # entry:42 is corrected to a benign status conference while entry:99 keeps the docket hydrated.
    court.stage = 2
    scan2 = await _scan(db, court, [client], notifier)
    assert scan2["rows_written"] == 1
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:42"]["match_status"] == "withdrawn"
    assert by_filing["entry:42"]["auto_linked"] is False
    assert by_filing["entry:42"]["bond_case_id"] == ""
    assert by_filing["entry:99"]["match_status"] == "confirmed"
    assert by_filing["entry:99"]["auto_linked"] is True

    # Now entry:99 is also corrected (0 classified events on the docket); seen_filing_ids still withdraws it.
    court.stage = 3
    scan3 = await _scan(db, court, [client], notifier)
    assert scan3["rows_written"] == 1
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:99"]["match_status"] == "withdrawn"
    assert by_filing["entry:99"]["auto_linked"] is False


@pytest.mark.asyncio
async def test_partial_hydration_failure_holds_search_checkpoint(monkeypatch):
    _enable(monkeypatch)

    class _PartialOutage(_CourtListener):
        def __init__(self):
            super().__init__(parties=[{"name": "Smith, John"}])
            self.r_starts = []
            self.parties_error_ids = {3002}
            self.parties_error = {"success": False, "error": "HTTP 503", "status_code": 503, "results": []}

        async def search_recent(self, **kwargs):
            self.calls.append(("search_recent", kwargs))
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            start_url = kwargs.get("start_url") or ""
            self.r_starts.append(start_url)
            if not start_url:
                return {
                    "success": True,
                    "truncated": True,
                    "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=slice2",
                    "results": [
                        _window_hit(3001, "2026-11-20", "Detention hearing", "2:24-cr-00301"),
                        _window_hit(3002, "2026-11-20", "Detention hearing", "2:24-cr-00302"),
                    ],
                }
            assert start_url.endswith("cursor=slice2")
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(3003, "2026-11-19", "Detention hearing", "2:24-cr-00303"),
                ],
            }

    court = _PartialOutage()
    db = _DB()

    first = await _scan(db, court, [_client()])
    assert first["success"] is True
    assert first["rows_written"] == 1
    assert {row["docket_id"] for row in db.docket_events.docs} == {"3001"}

    # Recover party hydration on Scan 2: 3002 drains from the stored slice without re-searching.
    court.parties_error_ids = set()
    second = await _scan(db, court, [_client()])
    assert second["rows_written"] == 1
    assert court.r_starts == [""]
    assert {row["docket_id"] for row in db.docket_events.docs} == {"3001", "3002"}

    # Now all dockets in slice 1 hydrated cleanly, so Scan 3 advances to slice 2.
    third = await _scan(db, court, [_client()])
    assert third["rows_written"] == 1
    assert court.r_starts[-1].endswith("cursor=slice2")
    assert {row["docket_id"] for row in db.docket_events.docs} == {"3001", "3002", "3003"}

    # Same hold applies to partial entry hydration failures.
    class _PartialEntry(_PartialOutage):
        def __init__(self):
            super().__init__()
            self.parties_error_ids = set()
            self.parties_error = None
            self.entries_error_ids = {3002}
            self.entries_error = {"success": False, "error": "HTTP 503", "status_code": 503, "results": []}

        async def search_recent(self, **kwargs):
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            start_url = kwargs.get("start_url") or ""
            self.r_starts.append(start_url)
            if not start_url:
                return {
                    "success": True,
                    "truncated": True,
                    "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=slice2",
                    "results": [
                        _window_hit(3001, "2026-11-20", "Detention hearing", "2:24-cr-00301"),
                        _hit(id=3002, docket_id=3002, docketNumber="2:24-cr-00302", recap_documents=[]),
                    ],
                }
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(3003, "2026-11-19", "Detention hearing", "2:24-cr-00303"),
                ],
            }

        async def list_docket_entries(self, docket_id, **kwargs):
            if docket_id in self.entries_error_ids:
                return self.entries_error
            if docket_id == 3001:
                return {"success": True, "truncated": False, "results": []}
            today = datetime.now(timezone.utc).date().isoformat()
            return {
                "success": True,
                "truncated": False,
                "results": [{"id": 70, "description": "Arrest warrant issued", "date_filed": today}],
            }

    e_court = _PartialEntry()
    e_db = _DB()
    e1 = await _scan(e_db, e_court, [_client()])
    assert e1["success"] is True
    assert e1["rows_written"] == 1
    assert {row["docket_id"] for row in e_db.docket_events.docs} == {"3001"}
    e_court.entries_error_ids = set()
    e2 = await _scan(e_db, e_court, [_client()])
    assert e2["rows_written"] == 1
    assert e_court.r_starts == [""]
    assert {row["docket_id"] for row in e_db.docket_events.docs} == {"3001", "3002"}


@pytest.mark.asyncio
async def test_failed_confirmed_notification_is_not_replayed_after_downgrade_or_closure(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")

    # Case A: bond closes before the failed confirmed notification is retried.
    db_a = _DB()
    db_a.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db_a.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    real_update_a = db_a.notifications.update_one

    async def fail_all(filt, update, upsert=False):
        raise RuntimeError("transient notification failure")

    db_a.notifications.update_one = fail_all
    court_a = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
        parties=[_defendant(dob="01/15/1985")],
    )
    first_a = await run_federal_bond_risk_scan(db_a, client=court_a, notifier=_Notifier(), pause_s=0)
    assert first_a["confirmed"] == 1
    assert db_a.notifications.docs == []

    db_a.active_bonds.docs[0]["status"] = "exonerated"
    db_a.notifications.update_one = real_update_a
    second_a = await run_federal_bond_risk_scan(db_a, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert second_a["skipped"] == "no_active_clients"
    assert len(db_a.notifications.docs) == 1
    assert db_a.notifications.docs[0]["metadata"]["match_status"] == "withdrawn"

    # Case B: party gains a conflicting DOB before the failed confirmed notification is retried.
    db_b = _DB()
    real_update_b = db_b.notifications.update_one
    db_b.notifications.update_one = fail_all
    court_b = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
        parties=[_defendant(dob="01/15/1985")],
    )
    first_b = await _scan(db_b, court_b, [_client(dobs=["1985-01-15"])])
    assert first_b["confirmed"] == 1
    assert db_b.notifications.docs == []

    court_b.parties = [{"name": "Smith, John", "extra_info": "DOB: 01/15/1985\nDOB: 02/02/1990"}]
    db_b.notifications.update_one = real_update_b
    second_b = await _scan(db_b, court_b, [_client(dobs=["1985-01-15"])])
    assert second_b["rows_written"] == 1
    assert len(db_b.notifications.docs) == 1
    assert db_b.notifications.docs[0]["metadata"]["match_status"] == "possible"


@pytest.mark.asyncio
async def test_partial_search_outage_holds_checkpoint_and_raises_from_cron(monkeypatch):
    from dashboard.cron import _run_federal_bond_risk

    _enable(monkeypatch)

    class _PartialSearchThenRecover(_CourtListener):
        def __init__(self):
            super().__init__(parties=[{"name": "Smith, John"}])
            self.fail_rd = True
            self.r_starts = []

        async def search_recent(self, **kwargs):
            self.calls.append(("search_recent", kwargs))
            stype = kwargs["search_type"]
            if stype == "rd" and self.fail_rd:
                return {"success": False, "error": "HTTP 403", "status_code": 403, "results": []}
            if stype != "r":
                return {"success": True, "results": [], "truncated": False}
            start_url = kwargs.get("start_url") or ""
            self.r_starts.append(start_url)
            if not start_url:
                return {
                    "success": True,
                    "truncated": True,
                    "resume_url": "https://www.courtlistener.com/api/rest/v4/search/?cursor=slice2",
                    "results": [
                        _window_hit(4001, "2026-11-20", "Detention hearing", "2:24-cr-00401"),
                    ],
                }
            assert start_url.endswith("cursor=slice2")
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(4002, "2026-11-19", "Detention hearing", "2:24-cr-00402"),
                ],
            }

    court = _PartialSearchThenRecover()
    db = _DB()

    first = await _scan(db, court, [_client()])
    assert first["success"] is True
    assert first["rows_written"] == 1
    assert first["incomplete_coverage"] is True
    assert first["truncated"] is True
    assert "rd" in first["warning"]
    assert "403" in first["warning"]
    assert {row["docket_id"] for row in db.docket_events.docs} == {"4001"}

    # Once rd recovers, Scan 2 re-reads slice 1 before advancing to slice 2.
    court.fail_rd = False
    second = await _scan(db, court, [_client()])
    assert second["incomplete_coverage"] is False
    assert court.r_starts == ["", ""]

    third = await _scan(db, court, [_client()])
    assert third["rows_written"] == 1
    assert court.r_starts[-1].endswith("cursor=slice2")
    assert {row["docket_id"] for row in db.docket_events.docs} == {"4001", "4002"}

    async def partial_scan(_db):
        return {
            "success": True,
            "skipped": None,
            "incomplete_coverage": True,
            "rows_written": 1,
            "confirmed": 0,
            "possible": 1,
            "warning": "CourtListener search failed for 1 source(s): rd HTTP 403",
        }

    async def paginated_scan(_db):
        return {
            "success": True,
            "skipped": None,
            "incomplete_coverage": False,
            "rows_written": 1,
            "confirmed": 0,
            "possible": 1,
            "warning": "Matching dockets remain after this hydration batch.",
        }

    monkeypatch.setattr("dashboard.extensions.get_db", lambda: object())
    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", partial_scan)
    with pytest.raises(RuntimeError, match="incomplete.*rd HTTP 403"):
        await _run_federal_bond_risk()

    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", paginated_scan)
    await _run_federal_bond_risk()


@pytest.mark.asyncio
async def test_match_audits_record_confidence_across_transitions(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")

    # Dual confirmation (DOB + case number) -> confidence 1.0
    dual_db = _DB()
    dual_court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    await _scan(dual_db, dual_court, [_client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])])
    assert dual_db.audit_events.docs[-1]["confidence"] == 1.0
    assert dual_db.audit_events.docs[-1]["new_state"]["confidence"] == 1.0
    assert dual_db.docket_events.docs[0]["confidence"] == 1.0

    # Ambiguous multi-client match -> confidence 0.25
    amb_db = _DB()
    amb_clients = [
        _client(name="John Smith", dobs=["1985-01-15"], bond="bond-a", defendant="def-a"),
        _client(name="John Smith", dobs=["1985-01-15"], bond="bond-b", defendant="def-b"),
    ]
    await _scan(amb_db, dual_court, amb_clients)
    assert len(amb_db.audit_events.docs) == 2
    assert all(row["confidence"] == 0.25 for row in amb_db.audit_events.docs)
    assert all(row["new_state"]["ambiguous"] is True for row in amb_db.audit_events.docs)

    # Lifecycle transitions: confirmed (0.95) -> withdrawn by closure (0.0) -> restored (0.95) -> entry corrected (0.0)
    today = datetime.now(timezone.utc).date().isoformat()

    class _CorrectingCourt(_CourtListener):
        def __init__(self):
            super().__init__(
                hits=[_hit(recap_documents=[])],
                parties=[_defendant(dob="01/15/1985")],
            )
            self.benign = False

        async def list_docket_entries(self, docket_id, **kwargs):
            desc = "Status conference" if self.benign else "Arrest warrant issued for John Smith"
            return {"success": True, "truncated": False, "results": [{"id": 88, "description": desc, "date_filed": today}]}

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    court = _CorrectingCourt()
    await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)

    db.active_bonds.docs[0]["status"] = "exonerated"
    await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    by_reason = {row["reason"]: row for row in db.audit_events.docs}
    assert by_reason["match_recorded"]["confidence"] == 0.95
    assert by_reason["client_no_longer_active"]["confidence"] == 0.0
    assert by_reason["client_no_longer_active"]["old_state"]["confidence"] == 0.95
    assert by_reason["client_no_longer_active"]["new_state"]["confidence"] == 0.0
    assert by_reason["client_no_longer_active"]["evidence"]["candidate_bond_case_id"] == "bond-1"

    db.active_bonds.docs[0]["status"] = "reinstated"
    await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    court.benign = True
    await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)

    reasons = [(row["reason"], row["confidence"], row["old_state"].get("confidence", 0.0), row["new_state"]["confidence"]) for row in db.audit_events.docs]
    assert ("withdrawn_restored", 0.95, 0.0, 0.95) in reasons
    assert ("identity_no_longer_matches", 0.0, 0.95, 0.0) in reasons


@pytest.mark.asyncio
async def test_bond_closing_mid_scan_is_not_stored_or_notified(monkeypatch):
    from dashboard.services.federal_bond_risk import _store_decision

    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")

    # Case A: bond closes during CourtListener search/hydration on a brand-new match.
    db_a = _DB()
    db_a.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db_a.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })

    class _CloseOnSearch(_CourtListener):
        def __init__(self, target_docs, idx=0):
            super().__init__(
                hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
                parties=[_defendant(dob="01/15/1985")],
            )
            self.target_docs = target_docs
            self.idx = idx

        async def search_recent(self, **kwargs):
            res = await super().search_recent(**kwargs)
            if kwargs.get("search_type") == "r":
                self.target_docs[self.idx]["status"] = "exonerated"
            return res

    notifier_a = _Notifier()
    res_a = await run_federal_bond_risk_scan(
        db_a, client=_CloseOnSearch(db_a.active_bonds.docs), notifier=notifier_a, pause_s=0,
    )
    assert res_a["rows_written"] == 0
    assert res_a["confirmed"] == 0
    assert db_a.docket_events.docs == []
    assert db_a.notifications.docs == []
    assert notifier_a.calls == []

    # Case B: bond closes during Scan 2 search after an existing confirmed row was stored on Scan 1.
    db_b = _DB()
    db_b.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db_b.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    scan1 = await run_federal_bond_risk_scan(
        db_b,
        client=_CourtListener(
            hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
            parties=[_defendant(dob="01/15/1985")],
        ),
        notifier=_Notifier(),
        pause_s=0,
    )
    assert scan1["confirmed"] == 1
    notifier_b2 = _Notifier()
    scan2 = await run_federal_bond_risk_scan(
        db_b, client=_CloseOnSearch(db_b.active_bonds.docs), notifier=notifier_b2, pause_s=0,
    )
    assert scan2["rows_written"] == 1
    assert scan2["confirmed"] == 0
    assert db_b.docket_events.docs[0]["match_status"] == "withdrawn"
    assert db_b.docket_events.docs[0]["auto_linked"] is False
    assert db_b.docket_events.docs[0]["bond_case_id"] == ""
    assert db_b.docket_events.docs[0]["confidence"] == 0.0
    assert any(
        a["reason"] == "client_no_longer_active" and a["confidence"] == 0.0
        for a in db_b.audit_events.docs
    )
    assert db_b.notifications.docs[-1]["metadata"]["match_status"] == "withdrawn"
    assert len(notifier_b2.calls) == 1
    assert notifier_b2.calls[0]["details"]["match_status"] == "withdrawn"
    assert notifier_b2.calls[0]["details"]["auto_linked"] is False

    # Case C: second bond closes right before its decision is stored in the persistence loop.
    db_c = _DB()
    db_c.active_bonds.docs.extend([
        {"_id": "bond-1", "Bond_Case_ID": "bond-1", "Defendant_ID": "def-1", "defendant_name": "John Smith", "status": "active"},
        {"_id": "bond-2", "Bond_Case_ID": "bond-2", "Defendant_ID": "def-2", "defendant_name": "Jane Roe", "status": "active"},
    ])
    db_c.defendants.docs.extend([
        {"_id": "def-1", "Defendant_ID": "def-1", "date_of_birth": "1985-01-15"},
        {"_id": "def-2", "Defendant_ID": "def-2", "date_of_birth": "1990-02-02"},
    ])

    class _TwoDocketCourt(_CourtListener):
        def __init__(self):
            super().__init__(
                hits=[
                    _hit(id=1001, docket_id=1001, caseName="United States v. John Smith", docketNumber="2:24-cr-00101"),
                    _hit(id=1002, docket_id=1002, caseName="United States v. Jane Roe", docketNumber="2:24-cr-00102"),
                ],
            )

        async def list_docket_parties(self, docket_id, **kwargs):
            if docket_id == 1001:
                return {"success": True, "truncated": False, "results": [_defendant(dob="01/15/1985")]}
            return {"success": True, "truncated": False, "results": [_defendant("Roe, Jane", dob="02/02/1990", docket_id=1002)]}

    real_update = db_c.docket_events.update_one
    closed_second = {"done": False}

    async def close_second_after_first(filt, update, upsert=False):
        res = await real_update(filt, update, upsert=upsert)
        if upsert and not closed_second["done"]:
            closed_second["done"] = True
            db_c.active_bonds.docs[1]["status"] = "exonerated"
        return res

    db_c.docket_events.update_one = close_second_after_first
    notifier_c = _Notifier()
    res_c = await run_federal_bond_risk_scan(db_c, client=_TwoDocketCourt(), notifier=notifier_c, pause_s=0)
    assert res_c["rows_written"] == 1
    assert res_c["confirmed"] == 1
    assert {row["bond_case_id"] for row in db_c.docket_events.docs} == {"bond-1"}
    assert {row["entity_id"] for row in db_c.notifications.docs} == {"bond-1"}
    assert {call["details"]["bond_case_id"] for call in notifier_c.calls} == {"bond-1"}

    # Direct _store_decision call with require_active_record=True also fails closed on inactive bonds.
    from dashboard.services.federal_bond_risk import MatchDecision

    stale = MatchDecision(
        client=_client(name="Jane Roe", dobs=["1990-02-02"], bond="bond-2", defendant="def-2"),
        status="confirmed",
        confirmations=("dob",),
        event_type="warrant",
        event_label="Warrant",
        filing_id="entry:55",
    )
    assert await _store_decision(
        db_c,
        stale,
        _docket_for(DocketParty("Roe, Jane", frozenset({"1990-02-02"}))),
        require_active_record=True,
    ) == "inactive"
    assert {row["bond_case_id"] for row in db_c.docket_events.docs} == {"bond-1"}


@pytest.mark.asyncio
async def test_actor_sensitive_phrase_must_tie_to_defendant(monkeypatch):
    from dashboard.services.federal_bond_risk import _events_from_text, decide_matches

    _enable(monkeypatch)
    party = DocketParty("Smith, John", frozenset({"1985-01-15"}))
    client = _client(name="John Smith", dobs=["1985-01-15"])

    negative_texts = (
        "John Smith was present; counsel for the government failed to appear",
        "John Smith was present, counsel for the government failed to appear",
        "Counsel for John Smith failed to appear",
        "Failure to appear by counsel for John Smith",
        "Arrest warrant issued for witness John Smith",
        "ORDER as to John Smith: counsel for the government failed to appear",
        "ORDER as to John Smith, counsel for the government failed to appear",
        "John Smith appeared; arrest warrant issued for material witness",
    )
    for text in negative_texts:
        events = _events_from_text(text, "entry:1", ["Smith, John"])
        assert events, text
        assert all(ev.affected_names == () for ev in events), (text, events)
        decisions = decide_matches([client], _docket_for(party, events=events))
        assert len(decisions) == 1, text
        assert decisions[0].status == "possible", (text, decisions)
        assert decisions[0].confirmations == (), (text, decisions)

    positive_texts = (
        "John Smith failed to appear",
        "Defendant John Smith has failed to appear",
        "Failure to appear by John Smith",
        "Arrest warrant issued for John Smith",
        "Arrest warrant issued as to Smith, John",
        "ORDER as to John Smith denying Motion to Modify Conditions of Release",
        "Motion by the Government to revoke bond as to John Smith",
        "Arrest warrant issued for John Smith and Jane Roe",
    )
    party_names = ["Smith, John", "Roe, Jane"]
    for text in positive_texts:
        events = _events_from_text(text, "entry:2", party_names)
        assert events, text
        assert any("Smith, John" in ev.affected_names for ev in events), (text, events)
        docket = _docket_for(party, events=events) if "Jane Roe" not in text else _docket_for(
            party, DocketParty("Roe, Jane", frozenset({"1990-02-02"})), events=events,
        )
        decisions = decide_matches([client], docket)
        assert decisions[0].status == "confirmed", (text, decisions)
        assert decisions[0].confirmations == ("dob",), (text, decisions)

    # End-to-end scan through _extend_events must also keep multi-clause counsel FTA as possible.
    court = _CourtListener(
        hits=[_hit(recap_documents=[{
            "id": 9,
            "description": "John Smith was present; counsel for the government failed to appear",
        }])],
        parties=[_defendant(dob="01/15/1985")],
    )
    db = _DB()
    result = await _scan(db, court, [client])
    assert result["rows_written"] == 1
    assert result["possible"] == 1
    assert result["confirmed"] == 0
    assert db.docket_events.docs[0]["match_status"] == "possible"
    assert db.docket_events.docs[0]["auto_linked"] is False


@pytest.mark.asyncio
async def test_stale_notification_insert_is_blocked_on_newer_revision(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _ensure_notification

    # Case 1: docket_events already moved to revision 2 before _ensure_notification runs for revision 1.
    db = _DB()
    db.docket_events.docs.append({
        "_id": "row-1",
        "source": SOURCE,
        "source_id": "src-rev",
        "match_status": "possible",
        "notification_revision": 2,
    })
    stale_note = {
        "title": "Federal bond risk linked",
        "metadata": {"source_id": "src-rev", "match_status": "confirmed"},
    }
    assert await _ensure_notification(db, stale_note, "src-rev", "confirmed", revision=1) is False
    assert db.notifications.docs == []

    # Case 2: concurrent scan advances revision from 1 to 2 while notifications.update_one is in flight.
    db_race = _DB()
    db_race.docket_events.docs.append({
        "_id": "row-2",
        "source": SOURCE,
        "source_id": "src-race",
        "match_status": "confirmed",
        "notification_revision": 1,
    })
    real_update = db_race.notifications.update_one

    async def advance_revision_during_insert(filt, update, upsert=False):
        res = await real_update(filt, update, upsert=upsert)
        db_race.docket_events.docs[0]["match_status"] = "possible"
        db_race.docket_events.docs[0]["notification_revision"] = 2
        return res

    db_race.notifications.update_one = advance_revision_during_insert
    assert await _ensure_notification(db_race, stale_note, "src-race", "confirmed", revision=1) is False
    assert db_race.notifications.docs == []


@pytest.mark.asyncio
async def test_same_status_evidence_change_persists_and_audits(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    db = _DB()
    client = _client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])

    scan1 = await _scan(db, court, [client])
    assert scan1["rows_written"] == 1
    assert scan1["confirmed"] == 1
    assert db.docket_events.docs[0]["confirmations"] == ["dob", "case_number"]
    assert db.docket_events.docs[0]["confidence"] == 1.0
    assert len(db.audit_events.docs) == 1

    # Lose DOB evidence while retaining case_number confirmation (same match_status="confirmed").
    court.parties = [_defendant("Smith, John")]
    scan2 = await _scan(db, court, [client])
    assert scan2["rows_written"] == 1
    assert scan2["confirmed"] == 1
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["confirmations"] == ["case_number"]
    assert db.docket_events.docs[0]["confidence"] == 0.95
    assert db.docket_events.docs[0]["match_history"][-1]["reason"] == "match_evidence_updated"
    assert len(db.audit_events.docs) == 2
    updated_audit = db.audit_events.docs[-1]
    assert updated_audit["reason"] == "match_evidence_updated"
    assert updated_audit["confidence"] == 0.95
    assert updated_audit["old_state"]["confirmations"] == ["dob", "case_number"]
    assert updated_audit["old_state"]["confidence"] == 1.0
    assert updated_audit["new_state"]["confirmations"] == ["case_number"]
    assert updated_audit["new_state"]["confidence"] == 0.95

    # Repeating with unchanged evidence writes 0 rows and no extra audit.
    scan3 = await _scan(db, court, [client])
    assert scan3["rows_written"] == 0
    assert len(db.audit_events.docs) == 2


@pytest.mark.asyncio
async def test_filing_scan_uses_only_recap_search_types_not_docket_only(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[{"name": "Smith, John"}],
    )
    db = _DB()
    result = await _scan(db, court, [_client()])
    assert result["success"] is True
    assert result["rows_written"] == 1
    searched_types = {
        kwargs["search_type"]
        for kind, kwargs in court.calls
        if kind == "search_recent"
    }
    assert searched_types == {"r", "rd"}


@pytest.mark.asyncio
async def test_pending_slice_id_is_unique_per_tenant(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    from dashboard.services.federal_bond_risk import (
        _clear_pending_slice,
        _load_pending_slice,
        _pending_slice_id,
        _save_pending_slice,
    )
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedDatabase

    class _States:
        def __init__(self):
            self.docs = []

        async def find_one(self, filt=None):
            for doc in self.docs:
                if all(doc.get(key) == value for key, value in (filt or {}).items()):
                    return dict(doc)
            return None

        async def update_one(self, filt, update, upsert=False):
            for doc in self.docs:
                if all(doc.get(key) == value for key, value in (filt or {}).items()):
                    doc.update((update or {}).get("$set") or {})
                    return type("R", (), {"matched_count": 1, "upserted_id": None})()
            if not upsert:
                return type("R", (), {"matched_count": 0, "upserted_id": None})()
            new_id = (filt or {}).get("_id")
            if any(doc.get("_id") == new_id for doc in self.docs):
                raise RuntimeError("E11000 duplicate key")
            stored = dict(filt or {})
            stored.update((update or {}).get("$set") or {})
            self.docs.append(stored)
            return type("R", (), {"matched_count": 0, "upserted_id": new_id})()

    class _Raw:
        def __init__(self):
            self.federal_bond_risk_state = _States()

    raw = _Raw()
    db = TenantScopedDatabase(raw)
    with bind_job_tenant("alpha", "federal_bond_risk"):
        assert _pending_slice_id() == "pending_slice:alpha"
        assert await _save_pending_slice(
            db, [{"id": "101", "hits": []}], [("r", "2026-11-20", "")], [], commit_checkpoints=True,
        ) is True
    with bind_job_tenant("beta", "federal_bond_risk"):
        assert _pending_slice_id() == "pending_slice:beta"
        assert await _load_pending_slice(db) is None
        assert await _save_pending_slice(
            db, [{"id": "202", "hits": []}], [], [], commit_checkpoints=True,
        ) is True
    with bind_job_tenant("alpha", "federal_bond_risk"):
        loaded_alpha = await _load_pending_slice(db)
        assert loaded_alpha is not None
        assert [row["id"] for row in loaded_alpha[0]] == ["101"]
        await _clear_pending_slice(db)
        assert await _load_pending_slice(db) is None
    with bind_job_tenant("beta", "federal_bond_risk"):
        loaded_beta = await _load_pending_slice(db)
        assert loaded_beta is not None
        assert [row["id"] for row in loaded_beta[0]] == ["202"]
    assert {doc["_id"] for doc in raw.federal_bond_risk_state.docs} == {
        "pending_slice:alpha",
        "pending_slice:beta",
    }


@pytest.mark.asyncio
async def test_failed_and_incomplete_cron_scans_send_operational_slack_alert(monkeypatch):
    from dashboard.cron import _run_federal_bond_risk

    sent = []

    class _SpyNotifier:
        def notify_alert(self, **kwargs):
            sent.append(kwargs)
            return True

    monkeypatch.setattr("writers.slack_notifier.SlackNotifier", _SpyNotifier)
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: object())

    async def failed(_db):
        return {
            "success": False,
            "skipped": "search_failed",
            "warning": "CourtListener search failed",
            "rows_written": 0,
        }

    async def incomplete(_db):
        return {
            "success": True,
            "skipped": None,
            "incomplete_coverage": True,
            "warning": "CourtListener search failed for 1 source(s): rd HTTP 403",
            "rows_written": 1,
            "confirmed": 0,
            "possible": 1,
        }

    async def crashed(_db):
        raise ConnectionError("network down")

    async def skipped(_db):
        return {"success": True, "skipped": "flag_off"}

    async def healthy(_db):
        return {
            "success": True,
            "skipped": None,
            "incomplete_coverage": False,
            "rows_written": 0,
            "confirmed": 0,
            "possible": 0,
            "warning": None,
        }

    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", skipped)
    await _run_federal_bond_risk()
    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", healthy)
    await _run_federal_bond_risk()
    assert sent == []

    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", failed)
    with pytest.raises(RuntimeError, match="search_failed"):
        await _run_federal_bond_risk()
    assert len(sent) == 1
    assert sent[-1]["level"] == "error"
    assert sent[-1]["source"] == "FederalBondRisk"
    assert sent[-1]["bridge"] is False
    assert sent[-1]["details"]["skipped"] == "search_failed"

    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", incomplete)
    with pytest.raises(RuntimeError, match="incomplete"):
        await _run_federal_bond_risk()
    assert len(sent) == 2
    assert sent[-1]["level"] == "warning"
    assert "incomplete coverage" in sent[-1]["title"].lower()
    assert sent[-1]["bridge"] is False

    monkeypatch.setattr("dashboard.services.federal_bond_risk.run_federal_bond_risk_scan", crashed)
    with pytest.raises(ConnectionError, match="network down"):
        await _run_federal_bond_risk()
    assert len(sent) == 3
    assert sent[-1]["level"] == "error"
    assert sent[-1]["details"] == {"error_type": "ConnectionError"}
    assert "network down" not in json.dumps(sent[-1])


@pytest.mark.asyncio
async def test_partial_entry_hydration_failure_does_not_withdraw_existing_link(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client(name="John Smith", dobs=["1985-01-15"])

    class _PartialEntryOutage(_CourtListener):
        def __init__(self):
            super().__init__(
                hits=[_hit(id=1001, docket_id=1001, recap_documents=[])],
                parties=[_defendant(dob="01/15/1985")],
            )
            self.stage = 1
            self.entries_error_ids = set()
            self.entries_error = {"success": False, "error": "HTTP 503", "status_code": 503, "results": []}

        async def search_recent(self, **kwargs):
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            if self.stage == 1:
                return {"success": True, "results": [_hit(id=1001, docket_id=1001, recap_documents=[])], "truncated": False}
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _hit(
                        id=1001,
                        docket_id=1001,
                        recap_documents=[{"id": 9, "docket_entry_id": 42, "description": "Order"}],
                    ),
                    _hit(
                        id=1002,
                        docket_id=1002,
                        docketNumber="2:24-cr-00456",
                        recap_documents=[{"id": 10, "description": "Detention hearing"}],
                    ),
                ],
            }

        async def list_docket_entries(self, docket_id, **kwargs):
            if docket_id in self.entries_error_ids:
                return self.entries_error
            if docket_id == 1002:
                return {"success": True, "truncated": False, "results": []}
            return {
                "success": True,
                "truncated": False,
                "results": [{"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": today}],
            }

    court = _PartialEntryOutage()
    db = _DB()

    scan1 = await _scan(db, court, [client])
    assert scan1["confirmed"] == 1
    row_1001 = [r for r in db.docket_events.docs if r["docket_id"] == "1001"][0]
    assert row_1001["match_status"] == "confirmed"
    assert row_1001["auto_linked"] is True
    assert row_1001["filing_id"] == "entry:42"

    # During Scan 2, docket 1001 returns a truncated benign RECAP snippet while entry hydration fails (1002 succeeds).
    court.stage = 2
    court.entries_error_ids = {1001}
    scan2 = await _scan(db, court, [client])
    assert scan2["success"] is True
    assert scan2["incomplete_coverage"] is True
    assert scan2["truncated"] is True
    row_1001 = [r for r in db.docket_events.docs if r["docket_id"] == "1001"][0]
    assert row_1001["match_status"] == "confirmed"
    assert row_1001["auto_linked"] is True

    # Recover entry hydration on Scan 3: pending slice drains cleanly and entry:42 stays confirmed.
    court.entries_error_ids = set()
    scan3 = await _scan(db, court, [client])
    assert scan3["incomplete_coverage"] is False
    row_1001 = [r for r in db.docket_events.docs if r["docket_id"] == "1001"][0]
    assert row_1001["match_status"] == "confirmed"
    assert row_1001["auto_linked"] is True


@pytest.mark.asyncio
async def test_restored_or_upgraded_row_retry_uses_confirmed_notification_not_downgrade(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")
    from dashboard.services.federal_bond_risk import _stored_notification

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    court = _CourtListener(
        hits=[_hit(recap_documents=[{"id": 9, "description": "Arrest warrant issued for John Smith"}])],
        parties=[_defendant(dob="01/15/1985")],
    )
    await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)

    db.active_bonds.docs[0]["status"] = "exonerated"
    await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert db.docket_events.docs[0]["match_status"] == "withdrawn"
    assert db.docket_events.docs[0]["downgrade_reason"] == "client_no_longer_active"

    # Reinstate bond while notifications.update_one fails on the immediate write.
    db.active_bonds.docs[0]["status"] = "reinstated"
    real_update = db.notifications.update_one

    async def fail_notify(filt, update, upsert=False):
        raise RuntimeError("notifications temporarily unavailable")

    db.notifications.update_one = fail_notify
    await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["downgrade_reason"] == ""
    assert db.docket_events.docs[0]["notification_written"] is False

    # Restore notifications on Scan 4: retry_pending_notifications writes a confirmed alert, not a withdrawal.
    db.notifications.update_one = real_update
    await run_federal_bond_risk_scan(db, client=_CourtListener(hits=[]), notifier=_Notifier(), pause_s=0)
    assert db.docket_events.docs[0]["notification_written"] is True
    assert db.docket_events.docs[0]["notification_status"] == "confirmed"
    retried = db.notifications.docs[-1]
    assert retried["metadata"]["match_status"] == "confirmed"
    assert retried["metadata"]["auto_linked"] is True
    assert "withdrawn" not in retried["title"].lower()

    # Even if a legacy confirmed row still carries stale downgrade_reason, _stored_notification ignores it.
    legacy_note = _stored_notification({
        "source_id": "legacy-1",
        "match_status": "confirmed",
        "auto_linked": True,
        "downgrade_reason": "client_no_longer_active",
        "title": "Federal bond risk linked — Warrant",
        "description": "Confirmed for John Smith.",
    })
    assert legacy_note["metadata"]["match_status"] == "confirmed"
    assert legacy_note["metadata"]["auto_linked"] is True


@pytest.mark.asyncio
async def test_upgrade_clears_stale_slack_claim_and_mismatch_releases_claim(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _claim_slack_delivery, _now

    # Case 1: active slack_claim from possible revision must not block the upgraded confirmed Slack post.
    court = _CourtListener(hits=[_hit()], parties=[{"name": "Smith, John"}])
    db = _DB()
    notifier1 = _Notifier()
    await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier1)
    assert db.docket_events.docs[0]["match_status"] == "possible"
    prior_claim_at = _now()
    db.docket_events.docs[0]["slack_delivered"] = False
    db.docket_events.docs[0]["slack_claim"] = True
    db.docket_events.docs[0]["slack_claim_at"] = prior_claim_at

    court.parties = [_defendant(dob="01/15/1985")]
    notifier2 = _Notifier()
    scan2 = await _scan(db, court, [_client(dobs=["1985-01-15"])], notifier2)
    assert scan2["slack_sent"] == 1
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["slack_delivered"] is True
    assert db.docket_events.docs[0]["slack_claim"] is False
    assert db.docket_events.docs[0]["slack_claim_at"] != prior_claim_at
    assert notifier2.calls[-1]["details"]["match_status"] == "confirmed"

    # Case 2: concurrent upgrade lands after update_one claims possible rev 0; mismatch must release the claim.
    db_race = _DB()
    db_race.docket_events.docs.append({
        "_id": "race-claim",
        "source": SOURCE,
        "source_id": "src-claim",
        "match_status": "possible",
        "notification_revision": 0,
        "slack_delivered": False,
        "slack_claim": False,
        "auto_linked": False,
    })
    real_find = db_race.docket_events.find_one
    flipped = {"done": False}

    async def upgrade_before_fresh_read(filt=None, projection=None, sort=None):
        if not flipped["done"] and filt == {"source": SOURCE, "source_id": "src-claim"}:
            flipped["done"] = True
            db_race.docket_events.docs[0]["match_status"] = "confirmed"
            db_race.docket_events.docs[0]["notification_revision"] = 1
            db_race.docket_events.docs[0]["auto_linked"] = True
        return await real_find(filt, projection, sort)

    db_race.docket_events.find_one = upgrade_before_fresh_read
    assert await _claim_slack_delivery(db_race, "src-claim", match_status="possible", revision=0) is None
    assert db_race.docket_events.docs[0]["slack_claim"] is False
    assert db_race.docket_events.docs[0]["slack_claim_at"] == ""
    claimed_confirmed = await _claim_slack_delivery(
        db_race, "src-claim", match_status="confirmed", revision=1,
    )
    assert claimed_confirmed is not None
    assert claimed_confirmed["match_status"] == "confirmed"


@pytest.mark.asyncio
async def test_stale_withdrawal_is_rejected_after_same_status_evidence_refresh(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import _withdraw_row

    court = _CourtListener(
        hits=[_hit()],
        parties=[_defendant(dob="01/15/1985")],
    )
    db = _DB()
    client = _client(dobs=["1985-01-15"], numbers=["2:24-cr-00123"])
    await _scan(db, court, [client])
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["match_revision"] == 0
    stale_row = dict(db.docket_events.docs[0])

    # Refresh evidence while keeping status="confirmed" -> match_revision advances to 1.
    court.parties = [_defendant("Smith, John")]
    await _scan(db, court, [client])
    assert db.docket_events.docs[0]["confirmations"] == ["case_number"]
    assert db.docket_events.docs[0]["confidence"] == 0.95
    assert db.docket_events.docs[0]["match_revision"] == 1
    assert db.docket_events.docs[0].get("notification_revision", 0) == 0

    audits_before = len(db.audit_events.docs)
    notes_before = len(db.notifications.docs)
    assert await _withdraw_row(db, stale_row, "identity_no_longer_matches") is False
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["confirmations"] == ["case_number"]
    assert db.docket_events.docs[0]["match_revision"] == 1
    assert len(db.audit_events.docs) == audits_before
    assert len(db.notifications.docs) == notes_before

    # Withdrawing with the fresh match_revision=1 snapshot succeeds.
    assert await _withdraw_row(db, dict(db.docket_events.docs[0]), "identity_no_longer_matches") is True
    assert db.docket_events.docs[0]["match_status"] == "withdrawn"
    assert db.docket_events.docs[0]["match_revision"] == 2


@pytest.mark.asyncio
async def test_unlink_rechecks_live_bond_status_before_withdrawing_reinstated_bond(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE, _unlink_inactive_bond_ids

    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "reinstated",
    })
    db.docket_events.docs.append({
        "_id": "evt-1",
        "source": SOURCE,
        "source_id": "fl-federal:1001:warrant:entry:1:bond-1",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-1",
        "candidate_bond_case_id": "bond-1",
        "defendant_id": "def-1",
        "matched_defendant_id": "def-1",
        "confirmations": ["dob"],
        "confidence": 0.95,
        "match_revision": 0,
        "notification_revision": 0,
    })

    # Stale snapshot omits bond-1, but live active_bonds shows reinstated -> no false unlink.
    assert await _unlink_inactive_bond_ids(db, set()) == []
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["auto_linked"] is True
    assert db.audit_events.docs == []
    assert db.notifications.docs == []

    # Lookup error during live bond check also skips withdrawal instead of false-unlinking.
    async def broken_find_one(*_args, **_kwargs):
        raise RuntimeError("mongo timeout")

    db.active_bonds.find_one = broken_find_one
    assert await _unlink_inactive_bond_ids(db, set()) == []
    assert db.docket_events.docs[0]["match_status"] == "confirmed"
    assert db.docket_events.docs[0]["auto_linked"] is True


@pytest.mark.asyncio
async def test_possible_match_refreshes_and_audits_when_candidate_identity_changes(monkeypatch):
    _enable(monkeypatch)
    court = _CourtListener(
        hits=[_hit()],
        parties=[{"name": "Smith, John"}],
    )
    db = _DB()
    notifier = _Notifier()

    scan1 = await _scan(
        db, court, [_client(name="John Smith", bond="bond-1", defendant="def-1")], notifier,
    )
    assert scan1["rows_written"] == 1
    assert scan1["possible"] == 1
    assert len(notifier.calls) == 1
    assert len(db.notifications.docs) == 1
    assert db.docket_events.docs[0]["match_status"] == "possible"
    assert db.docket_events.docs[0]["auto_linked"] is False
    assert db.docket_events.docs[0]["candidate_defendant_id"] == "def-1"
    assert db.docket_events.docs[0]["defendant_name"] == "John Smith"
    assert db.docket_events.docs[0]["match_revision"] == 0
    assert db.docket_events.docs[0]["notification_revision"] == 0

    # Active bond relinked to corrected defendant identity while filing remains "possible".
    relinked = ClientIdentity(
        bond_case_id="bond-1",
        defendant_id="def-2",
        names=("John A. Smith", "John Smith"),
        dobs=frozenset(),
        numbers=frozenset(),
        status="active",
    )
    scan2 = await _scan(db, court, [relinked], notifier)
    assert scan2["rows_written"] == 1
    assert scan2["possible"] == 1
    assert scan2["slack_sent"] == 1
    row = db.docket_events.docs[0]
    assert row["match_status"] == "possible"
    assert row["auto_linked"] is False
    assert row["candidate_defendant_id"] == "def-2"
    assert row["defendant_name"] == "John A. Smith"
    assert "John A. Smith" in row["description"]
    assert row["match_revision"] == 1
    assert row["notification_revision"] == 1
    assert len(db.notifications.docs) == 2
    assert "John A. Smith" in db.notifications.docs[-1]["message"]
    assert len(notifier.calls) == 2
    assert "John A. Smith" in notifier.calls[-1]["message"]
    assert row["match_history"][-1]["reason"] == "match_evidence_updated"
    assert row["match_history"][-1]["candidate_defendant_id"] == "def-2"
    updated_audit = db.audit_events.docs[-1]
    assert updated_audit["reason"] == "match_evidence_updated"
    assert updated_audit["old_state"]["candidate_defendant_id"] == "def-1"
    assert updated_audit["old_state"]["defendant_name"] == "John Smith"
    assert updated_audit["new_state"]["candidate_defendant_id"] == "def-2"
    assert updated_audit["new_state"]["defendant_name"] == "John A. Smith"

    # Repeating with unchanged candidate identity writes 0 rows and no extra alert/audit.
    scan3 = await _scan(db, court, [relinked], notifier)
    assert scan3["rows_written"] == 0
    assert scan3["slack_sent"] == 0
    assert len(db.notifications.docs) == 2
    assert len(notifier.calls) == 2
    assert len(db.audit_events.docs) == 2


@pytest.mark.asyncio
async def test_deleted_in_window_entry_withdraws_link_but_out_of_window_entry_stays(monkeypatch):
    _enable(monkeypatch)
    from dashboard.services.federal_bond_risk import SOURCE

    today = datetime.now(timezone.utc).date().isoformat()
    old_day = (datetime.now(timezone.utc).date() - timedelta(days=60)).isoformat()
    client = _client(name="John Smith", dobs=["1985-01-15"])

    class _DeletedEntryCourt(_CourtListener):
        def __init__(self):
            super().__init__(
                hits=[_hit(recap_documents=[])],
                parties=[_defendant(dob="01/15/1985")],
            )
            self.stage = 1

        async def list_docket_entries(self, docket_id, **kwargs):
            self.calls.append(("entries", kwargs))
            if self.stage == 1:
                rows = [
                    {"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": today},
                    {"id": 99, "description": "Detention hearing", "date_filed": today},
                ]
            elif self.stage == 2:
                rows = [
                    {"id": 99, "description": "Detention hearing", "date_filed": today},
                ]
            else:
                rows = []
            return {"success": True, "truncated": False, "results": rows}

    court = _DeletedEntryCourt()
    db = _DB()
    db.active_bonds.docs.append({
        "_id": "bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    db.defendants.docs.append({
        "_id": "def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })

    scan1 = await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    assert scan1["confirmed"] == 2
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:42"]["filing_date"] == today
    assert by_filing["entry:42"]["match_status"] == "confirmed"
    assert by_filing["entry:99"]["filing_date"] == today
    assert by_filing["entry:99"]["match_status"] == "confirmed"

    # Seed an older out-of-window confirmed entry on the same docket (outside 45-day lookback).
    db.docket_events.docs.append({
        "_id": "old-entry-7",
        "source": SOURCE,
        "source_id": "fl-federal:1001:warrant:entry:7:bond-1",
        "docket_id": "1001",
        "docket_number": "2:24-cr-00123",
        "filing_id": "entry:7",
        "filing_date": old_day,
        "event_type": "warrant",
        "match_status": "confirmed",
        "auto_linked": True,
        "bond_case_id": "bond-1",
        "candidate_bond_case_id": "bond-1",
        "defendant_id": "def-1",
        "candidate_defendant_id": "def-1",
        "matched_defendant_id": "def-1",
        "defendant_name": "John Smith",
        "confirmations": ["dob"],
        "confidence": 0.95,
        "slack_delivered": True,
        "notification_written": True,
        "notification_status": "confirmed",
        "match_revision": 0,
        "notification_revision": 0,
    })

    # Stage 2: entry:42 disappeared (deleted/sealed) while entry:99 keeps the docket hydrated cleanly.
    court.stage = 2
    scan2 = await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    assert scan2["rows_written"] == 1
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:42"]["match_status"] == "withdrawn"
    assert by_filing["entry:42"]["auto_linked"] is False
    assert by_filing["entry:42"]["bond_case_id"] == ""
    assert by_filing["entry:42"]["downgrade_reason"] == "filing_removed_from_docket"
    assert by_filing["entry:99"]["match_status"] == "confirmed"
    assert by_filing["entry:7"]["match_status"] == "confirmed"
    assert by_filing["entry:7"]["auto_linked"] is True
    removed_audits = [a for a in db.audit_events.docs if a.get("reason") == "filing_removed_from_docket"]
    assert len(removed_audits) == 1
    assert removed_audits[0]["confidence"] == 0.0

    # Stage 3: entry:99 also disappears leaving entries=[]; in-window entry:99 withdraws, old entry:7 stays.
    court.stage = 3
    scan3 = await run_federal_bond_risk_scan(db, client=court, notifier=_Notifier(), pause_s=0)
    assert scan3["rows_written"] == 1
    by_filing = {row["filing_id"]: row for row in db.docket_events.docs}
    assert by_filing["entry:99"]["match_status"] == "withdrawn"
    assert by_filing["entry:99"]["downgrade_reason"] == "filing_removed_from_docket"
    assert by_filing["entry:7"]["match_status"] == "confirmed"
    assert by_filing["entry:7"]["auto_linked"] is True
    assert client.bond_case_id == "bond-1"


@pytest.mark.asyncio
async def test_enabling_multi_tenant_preserves_shamrock_source_ids_and_isolates_other_tenants(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "test-token")
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    from dashboard.services.federal_bond_risk import (
        _pending_slice_id,
        _save_seed_offset,
        _search_before_id,
        _seed_cursor_id,
        _seed_offset,
    )
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedDatabase

    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": today}],
    )
    raw = _DB()
    raw.active_bonds.docs.append({
        "_id": "shamrock-bond-1",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    raw.defendants.docs.append({
        "_id": "shamrock-def-1",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    notifier = _Notifier()

    # Phase 1: Single-tenant mode (SAAS_MULTI_TENANT off).
    scan1 = await run_federal_bond_risk_scan(raw, client=court, notifier=notifier, pause_s=0)
    assert scan1["confirmed"] == 1
    assert len(notifier.calls) == 1
    assert len(raw.docket_events.docs) == 1
    assert raw.docket_events.docs[0]["source_id"] == "fl-federal:1001:warrant:entry:42:bond-1"
    assert len(raw.notifications.docs) == 1
    assert raw.notifications.docs[0]["claim_key"] == "fl-federal:1001:warrant:entry:42:bond-1:confirmed:0"
    await _save_seed_offset(raw, 4)

    # Phase 2: Backfill tenant_id="shamrock" on pre-tenancy rows (preserving source_id/claim_key/_id) and flip flag on.
    for collection_name in (
        "active_bonds",
        "defendants",
        "docket_events",
        "notifications",
        "audit_events",
        "federal_bond_risk_state",
    ):
        for doc in getattr(raw, collection_name).docs:
            doc["tenant_id"] = "shamrock"
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = TenantScopedDatabase(raw)

    with bind_job_tenant("shamrock", "federal_bond_risk"):
        assert _search_before_id("r") == "search_before:r"
        assert _seed_cursor_id() == "seed_cursor"
        assert _pending_slice_id() == "pending_slice"
        assert await _seed_offset(db) == 4
        scan2 = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    assert scan2["rows_written"] == 0
    assert scan2["slack_sent"] == 0
    assert len(notifier.calls) == 1
    assert len(raw.docket_events.docs) == 1
    assert len(raw.notifications.docs) == 1
    assert len(raw.audit_events.docs) == 1

    # Phase 3: Second tenant on the same shared DB uses distinct tenant-scoped source_id / claim_key values.
    raw.active_bonds.docs.append({
        "_id": "alpha-bond-1",
        "tenant_id": "alpha",
        "Bond_Case_ID": "bond-1",
        "Defendant_ID": "def-1",
        "defendant_name": "John Smith",
        "status": "active",
    })
    raw.defendants.docs.append({
        "_id": "alpha-def-1",
        "tenant_id": "alpha",
        "Defendant_ID": "def-1",
        "date_of_birth": "1985-01-15",
    })
    with bind_job_tenant("alpha", "federal_bond_risk"):
        assert _search_before_id("r") == "search_before:r:alpha"
        assert _seed_cursor_id() == "seed_cursor:alpha"
        assert _pending_slice_id() == "pending_slice:alpha"
        scan3 = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    assert scan3["rows_written"] == 1
    assert scan3["confirmed"] == 1
    assert scan3["slack_sent"] == 1
    assert {doc["source_id"] for doc in raw.docket_events.docs} == {
        "fl-federal:1001:warrant:entry:42:bond-1",
        "alpha:fl-federal:1001:warrant:entry:42:bond-1",
    }
    assert {doc["claim_key"] for doc in raw.notifications.docs} == {
        "fl-federal:1001:warrant:entry:42:bond-1:confirmed:0",
        "alpha:fl-federal:1001:warrant:entry:42:bond-1:confirmed:0",
    }

    with bind_job_tenant("alpha", "federal_bond_risk"):
        scan4 = await run_federal_bond_risk_scan(db, client=court, notifier=notifier, pause_s=0)
    assert scan4["rows_written"] == 0
    assert scan4["slack_sent"] == 0
    assert len(notifier.calls) == 2


@pytest.mark.asyncio
async def test_default_pause_and_inter_request_pacing_respect_courtlistener_minute_limit(monkeypatch):
    import asyncio
    import inspect
    import dashboard.services.federal_bond_risk as fbr
    from dashboard.cron import CRON_REGISTRY

    _enable(monkeypatch)
    sig = inspect.signature(fbr.run_federal_bond_risk_scan)
    assert sig.parameters["pause_s"].default == fbr.DEFAULT_PAUSE_S
    assert fbr.DEFAULT_PAUSE_S >= 12.0
    assert fbr.MAX_REQUESTS_PER_MINUTE <= 5
    assert fbr.MAX_REQUESTS_PER_HOUR <= 50
    assert fbr.MAX_REQUESTS_PER_DAY <= 125
    assert fbr.MAX_REQUESTS_PER_RUN <= 25
    cron_job = next(row for row in CRON_REGISTRY if row.name == "federal_bond_risk")
    assert cron_job.interval >= fbr.MAX_REQUESTS_PER_RUN * fbr.DEFAULT_PAUSE_S

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    today = datetime.now(timezone.utc).date().isoformat()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": today}],
    )
    db = _DB()
    result = await fbr.run_federal_bond_risk_scan(
        db,
        client=court,
        notifier=_Notifier(),
        pause_s=fbr.DEFAULT_PAUSE_S,
        clients=[_client(dobs=["1985-01-15"])],
    )
    assert result["confirmed"] == 1
    assert result["requests_made"] == 5
    assert len(sleeps) == 4
    assert all(delay >= 12.0 for delay in sleeps)


@pytest.mark.asyncio
async def test_per_run_request_budget_saves_pending_slice_and_drains_on_next_scan(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()

    class _TwoDocketCourt(_CourtListener):
        def __init__(self):
            super().__init__(parties=[{"name": "Smith, John", "extra_info": "DOB: 01/15/1985"}])
            self.search_runs = 0

        async def search_recent(self, **kwargs):
            self.calls.append(("search_recent", kwargs))
            if kwargs["search_type"] != "r":
                return {"success": True, "results": [], "truncated": False}
            self.search_runs += 1
            return {
                "success": True,
                "truncated": False,
                "results": [
                    _window_hit(3001, today, "Detention hearing", "2:24-cr-00301"),
                    _window_hit(3002, today, "Detention hearing", "2:24-cr-00302"),
                ],
            }

        async def get_docket_brief(self, docket_id):
            self.calls.append(("brief", docket_id))
            num = f"2:24-cr-00{docket_id - 2700}"
            return {
                "success": True,
                "status_code": 200,
                "data": {"id": docket_id, "court_id": "flmd", "docket_number": num, "case_name": "United States v. John Smith"},
            }

        async def list_docket_entries(self, docket_id, **kwargs):
            self.calls.append(("entries", docket_id))
            return {
                "success": True,
                "truncated": False,
                "results": [{"id": docket_id + 10, "description": "Detention hearing", "date_filed": today}],
            }

    court = _TwoDocketCourt()
    db = _DB()
    notifier = _Notifier()
    client = _client(dobs=["1985-01-15"])

    scan1 = await run_federal_bond_risk_scan(
        db, client=court, notifier=notifier, pause_s=0, clients=[client], max_requests=6,
    )
    assert scan1["success"] is True
    assert scan1["truncated"] is True
    assert scan1["incomplete_coverage"] is False
    assert scan1["requests_made"] == 5
    assert "request budget" in scan1["warning"]
    assert {doc["docket_id"] for doc in db.docket_events.docs} == {"3001"}

    # Scan 2 drains docket 3002 from pending_slice without re-searching CourtListener.
    scan2 = await run_federal_bond_risk_scan(
        db, client=court, notifier=notifier, pause_s=0, clients=[client], max_requests=6,
    )
    assert scan2["success"] is True
    assert scan2["incomplete_coverage"] is False
    assert scan2["requests_made"] == 3
    assert court.search_runs == 1
    assert {doc["docket_id"] for doc in db.docket_events.docs} == {"3001", "3002"}


@pytest.mark.asyncio
async def test_durable_hourly_and_daily_quota_defers_scans_and_shares_by_api_account(monkeypatch):
    import time
    from dashboard.services.federal_bond_risk import (
        MAX_REQUESTS_PER_DAY,
        MAX_REQUESTS_PER_HOUR,
        _courtlistener_account_key,
        _request_quota_id,
    )
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedDatabase

    _enable(monkeypatch)
    now = time.time()
    court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": datetime.now(timezone.utc).date().isoformat()}],
    )
    db = _DB()

    # Hourly quota exhausted (50 calls 10 seconds ago) -> skips with zero CourtListener HTTP calls.
    db.federal_bond_risk_state.docs.append({
        "_id": _request_quota_id(),
        "timestamps": [now - 10.0] * MAX_REQUESTS_PER_HOUR,
    })
    hourly_blocked = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert hourly_blocked["success"] is True
    assert hourly_blocked["skipped"] == "quota_exhausted"
    assert hourly_blocked["truncated"] is True
    assert hourly_blocked["incomplete_coverage"] is False
    assert hourly_blocked["requests_made"] == 0
    assert court.calls == []

    # Daily quota exhausted outside the 1-hour window (125 calls 2-10 hours ago) -> also skips with zero calls.
    db.federal_bond_risk_state.docs[0]["timestamps"] = [
        now - 7200.0 - (idx * 200.0) for idx in range(MAX_REQUESTS_PER_DAY)
    ]
    daily_blocked = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert daily_blocked["success"] is True
    assert daily_blocked["skipped"] == "quota_exhausted"
    assert daily_blocked["truncated"] is True
    assert daily_blocked["incomplete_coverage"] is False
    assert daily_blocked["requests_made"] == 0
    assert court.calls == []

    # Once old timestamps fall outside the 24-hour window, the scan proceeds and records new requests.
    db.federal_bond_risk_state.docs[0]["timestamps"] = [now - 90000.0] * MAX_REQUESTS_PER_DAY
    recovered = await _scan(db, court, [_client(dobs=["1985-01-15"])])
    assert recovered["skipped"] is None
    assert recovered["confirmed"] == 1
    assert recovered["requests_made"] == 5
    assert len(db.federal_bond_risk_state.docs[0]["timestamps"]) == 5

    # Tenants sharing the same CourtListener API token share one hashed quota key and budget;
    # distinct tokens keep separate account keys without exposing raw tokens.
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    with bind_job_tenant("shamrock", "federal_bond_risk"):
        shamrock_qid = _request_quota_id()
    with bind_job_tenant("alpha", "federal_bond_risk"):
        alpha_qid = _request_quota_id()
    assert shamrock_qid == alpha_qid
    assert "test-token" not in shamrock_qid
    assert shamrock_qid != _request_quota_id(_courtlistener_account_key(api_token="other-token"))

    shared_raw = _DB()
    shared_raw.federal_bond_risk_state.docs.append({
        "_id": shamrock_qid,
        "timestamps": [now - 10.0] * (MAX_REQUESTS_PER_HOUR - 5),
        "quota_revision": 0,
    })
    scoped_db = TenantScopedDatabase(shared_raw)
    shared_court = _CourtListener(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 42, "description": "Arrest warrant issued for John Smith", "date_filed": datetime.now(timezone.utc).date().isoformat()}],
    )
    with bind_job_tenant("shamrock", "federal_bond_risk"):
        shamrock_res = await _scan(scoped_db, shared_court, [_client(dobs=["1985-01-15"])])
    assert shamrock_res["confirmed"] == 1
    assert shamrock_res["requests_made"] == 5

    alpha_calls_before = len(shared_court.calls)
    with bind_job_tenant("alpha", "federal_bond_risk"):
        alpha_res = await _scan(scoped_db, shared_court, [_client(dobs=["1985-01-15"])])
    assert alpha_res["success"] is True
    assert alpha_res["skipped"] == "quota_exhausted"
    assert alpha_res["requests_made"] == 0
    assert len(shared_court.calls) == alpha_calls_before
    # Business rows written by Shamrock remain strictly tenant-isolated.
    assert [doc["tenant_id"] for doc in shared_raw.docket_events.docs] == ["shamrock"]


@pytest.mark.asyncio
async def test_non_defendant_party_roles_are_excluded_and_missing_or_ambiguous_roles_stay_possible(monkeypatch):
    _enable(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client(name="John Smith", dobs=["1985-01-15"], numbers=["2:24-cr-00123"])

    # 1. Explicit non-defendant role on this docket (even if Defendant on a different docket) is excluded.
    for role_name in ("Material Witness", "Victim", "Plaintiff", "Surety", "Attorney for Defendant"):
        court = _CourtListener(
            hits=[_hit(recap_documents=[])],
            parties=[{
                "name": "Smith, John",
                "party_types": [
                    {"docket_id": 9999, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
                    {"docket_id": 1001, "name": role_name, "extra_info": "DOB: 01/15/1985"},
                ],
            }],
            entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
        )
        db = _DB()
        res = await _scan(db, court, [client])
        assert res["rows_written"] == 0, role_name
        assert db.docket_events.docs == [], role_name

    # 2. Missing party_types OR blank party_types[].name stays "possible" (never confirmed).
    for parties in (
        [{"name": "Smith, John", "extra_info": "DOB: 01/15/1985"}],
        [{"name": "Smith, John", "party_types": [{"docket_id": 1001, "extra_info": "DOB: 01/15/1985"}]}],
    ):
        court = _CourtListener(
            hits=[_hit(recap_documents=[])],
            parties=parties,
            entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
        )
        db = _DB()
        res = await _scan(db, court, [client])
        assert res["confirmed"] == 0
        assert res["possible"] == 1
        assert db.docket_events.docs[0]["match_status"] == "possible"
        assert db.docket_events.docs[0]["auto_linked"] is False
        assert db.docket_events.docs[0]["confirmations"] == []

    # 3. Ambiguous or mixed roles on this docket also stay "possible".
    for party_types in (
        [{"docket_id": 1001, "name": "Other Party", "extra_info": "DOB: 01/15/1985"}],
        [
            {"docket_id": 1001, "name": "Defendant", "extra_info": "DOB: 01/15/1985"},
            {"docket_id": 1001, "name": "Victim", "extra_info": "DOB: 01/15/1985"},
        ],
    ):
        court = _CourtListener(
            hits=[_hit(recap_documents=[])],
            parties=[{"name": "Smith, John", "party_types": party_types}],
            entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
        )
        db = _DB()
        res = await _scan(db, court, [client])
        assert res["confirmed"] == 0
        assert res["possible"] == 1
        assert db.docket_events.docs[0]["match_status"] == "possible"
        assert db.docket_events.docs[0]["auto_linked"] is False

    # 4. Explicit criminal Defendant / Co-Defendant on docket 1001 confirms (even if Witness elsewhere).
    for def_role in ("Defendant", "Criminal Defendant", "Co-Defendant"):
        court = _CourtListener(
            hits=[_hit(recap_documents=[])],
            parties=[{
                "name": "Smith, John",
                "party_types": [
                    {"docket_id": 9999, "name": "Material Witness", "extra_info": "DOB: 01/15/1985"},
                    {"docket_id": 1001, "name": def_role, "extra_info": "DOB: 01/15/1985"},
                ],
            }],
            entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
        )
        db = _DB()
        res = await _scan(db, court, [client])
        assert res["confirmed"] == 1
        assert db.docket_events.docs[0]["match_status"] == "confirmed"
        assert db.docket_events.docs[0]["auto_linked"] is True
        assert db.docket_events.docs[0]["confirmations"] == ["dob", "case_number"]


@pytest.mark.asyncio
async def test_overlapping_scans_serialize_with_lease_and_reserve_quota_atomically(monkeypatch):
    import asyncio
    import time
    from dashboard.services.federal_bond_risk import (
        MAX_REQUESTS_PER_HOUR,
        _RequestQuota,
        _request_quota_id,
        _scan_lease_id,
    )
    from dashboard.tenancy.context import bind_job_tenant

    _enable(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    today = datetime.now(timezone.utc).date().isoformat()

    class _BlockingCourt(_CourtListener):
        async def search_recent(self, **kwargs):
            if kwargs["search_type"] == "r":
                entered.set()
                await release.wait()
            return await super().search_recent(**kwargs)

    court = _BlockingCourt(
        hits=[_hit(recap_documents=[])],
        parties=[_defendant(dob="01/15/1985")],
        entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
    )
    db = _DB()
    client = _client(dobs=["1985-01-15"])

    # 1. Overlapping scheduled + manual scans serialize cleanly via scan_lease.
    first_task = asyncio.create_task(_scan(db, court, [client]))
    await entered.wait()
    overlapped = await _scan(db, _CourtListener(hits=[]), [client])
    assert overlapped["success"] is True
    assert overlapped["skipped"] == "scan_in_progress"
    assert overlapped["requests_made"] == 0
    release.set()
    first = await first_task
    assert first["confirmed"] == 1

    # Lease is released once first scan completes, and expired leases can be reclaimed.
    db.federal_bond_risk_state.update_one  # ensure collection present
    lease_doc = next(d for d in db.federal_bond_risk_state.docs if d["_id"] == _scan_lease_id())
    lease_doc["lease_owner"] = "crashed-worker"
    lease_doc["lease_expires_at"] = "2020-01-01T00:00:00+00:00"
    recovered = await _scan(db, _CourtListener(hits=[]), [client])
    assert recovered["skipped"] is None

    # 2. Atomic CAS reserve + reconcile under concurrent contention enforces 50/hr ceiling.
    qdb = _DB()
    now = time.time()
    qdb.federal_bond_risk_state.docs.append({
        "_id": _request_quota_id(),
        "timestamps": [now - 30.0] * 30,
        "quota_revision": 0,
    })
    real_find = qdb.federal_bond_risk_state.find_one
    raced = {"injected": False}

    async def contend_once(filt=None, projection=None, sort=None):
        row = await real_find(filt, projection, sort)
        if not raced["injected"] and (filt or {}).get("_id") == _request_quota_id():
            raced["injected"] = True
            # Concurrent caller reserves 15 slots before caller A completes CAS update.
            quota_b = _RequestQuota(timestamps=[])
            granted_b, ts_b = await quota_b.reserve(qdb, 15, min_required=1)
            assert granted_b == 15
            assert ts_b > 0
        return row

    qdb.federal_bond_risk_state.find_one = contend_once
    quota_a = _RequestQuota(timestamps=[])
    granted_a, ts_a = await quota_a.reserve(qdb, 15, min_required=1)
    assert granted_a == MAX_REQUESTS_PER_HOUR - 30 - 15  # strictly 5 remaining, never >50/hr
    stored_row = await real_find({"_id": _request_quota_id()})
    assert len(stored_row["timestamps"]) == MAX_REQUESTS_PER_HOUR

    # Refunding 3 unused slots from caller A preserves all prior/caller-B timestamps.
    await quota_a.reconcile(qdb, granted_a, ts_a, 2)
    stored_after = await real_find({"_id": _request_quota_id()})
    assert len(stored_after["timestamps"]) == MAX_REQUESTS_PER_HOUR - 3

    # 3. Scan lease keys share one CourtListener API account key across tenants
    #    and actually serialize overlapping multi-tenant scans using the same token.
    from dashboard.services.federal_bond_risk import _courtlistener_account_key
    from dashboard.tenancy.scope import TenantScopedDatabase

    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    with bind_job_tenant("shamrock", "federal_bond_risk"):
        shamrock_lease_id = _scan_lease_id()
    with bind_job_tenant("alpha", "federal_bond_risk"):
        alpha_lease_id = _scan_lease_id()
    assert shamrock_lease_id == alpha_lease_id
    assert "test-token" not in shamrock_lease_id
    assert shamrock_lease_id != _scan_lease_id(_courtlistener_account_key(api_token="other-token"))

    mt_raw = _DB()
    mt_db = TenantScopedDatabase(mt_raw)
    mt_entered = asyncio.Event()
    mt_release = asyncio.Event()

    class _MtBlockingCourt(_CourtListener):
        async def search_recent(self, **kwargs):
            if kwargs["search_type"] == "r":
                mt_entered.set()
                await mt_release.wait()
            return await super().search_recent(**kwargs)

    async def _run_shamrock():
        with bind_job_tenant("shamrock", "federal_bond_risk"):
            return await _scan(
                mt_db,
                _MtBlockingCourt(
                    hits=[_hit(recap_documents=[])],
                    parties=[_defendant(dob="01/15/1985")],
                    entries=[{"id": 42, "description": "Detention hearing", "date_filed": today}],
                ),
                [client],
            )

    shamrock_task = asyncio.create_task(_run_shamrock())
    await mt_entered.wait()
    alpha_court = _CourtListener(hits=[])
    with bind_job_tenant("alpha", "federal_bond_risk"):
        alpha_overlapped = await _scan(mt_db, alpha_court, [client])
    assert alpha_overlapped["success"] is True
    assert alpha_overlapped["skipped"] == "scan_in_progress"
    assert alpha_overlapped["requests_made"] == 0
    assert alpha_court.calls == []
    mt_release.set()
    shamrock_done = await shamrock_task
    assert shamrock_done["confirmed"] == 1


@pytest.mark.asyncio
async def test_client_retries_reserve_quota_before_sending_and_stop_when_exhausted(monkeypatch):
    import time
    import httpx
    from dashboard.services.federal_bond_risk import (
        MAX_REQUESTS_PER_HOUR,
        _courtlistener_account_key,
        _request_quota_id,
    )

    _enable(monkeypatch)
    retry_sleeps = []

    async def fake_sleep(seconds):
        retry_sleeps.append(seconds)

    monkeypatch.setattr("dashboard.services.courtlistener_client.asyncio.sleep", fake_sleep)

    class _Response:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def json(self):
            return self._payload

    now = time.time()
    account_key = _courtlistener_account_key(api_token="test-token")
    quota_id = _request_quota_id(account_key)

    # 1. HTTP 429 on the last hourly slot: retry attempt is denied BEFORE sleeping or sending HTTP.
    calls_429 = []
    retry_sleeps.clear()

    class _HTTP429:
        is_closed = False

        async def get(self, url, params=None):
            calls_429.append((url, dict(params or {})))
            if (params or {}).get("type") == "r":
                return _Response(200, {"results": []})
            return _Response(429, {"detail": "throttled"}, {"Retry-After": "5"})

    db_429 = _DB()
    db_429.federal_bond_risk_state.docs.append({
        "_id": quota_id,
        "timestamps": [now - 10.0] * (MAX_REQUESTS_PER_HOUR - 2),
        "quota_revision": 0,
    })
    client_429 = CourtListenerClient(api_token="test-token")
    client_429._in_maintenance_window = lambda: False
    client_429._client = _HTTP429()
    res_429 = await run_federal_bond_risk_scan(
        db_429, client=client_429, notifier=_Notifier(), pause_s=0, clients=[_client()],
    )
    assert res_429["success"] is False
    assert res_429["skipped"] == "rate_limited"
    assert res_429["rows_written"] == 0
    assert len(calls_429) == 2  # r=1 attempt + rd=1 attempt; rd retry never hit the wire
    assert retry_sleeps == []
    assert len(db_429.federal_bond_risk_state.docs[0]["timestamps"]) == MAX_REQUESTS_PER_HOUR

    # 2. ReadTimeout on the last hourly slot: retry attempt is also denied BEFORE sleeping or sending HTTP.
    calls_timeout = []
    retry_sleeps.clear()

    class _HTTPTimeout:
        is_closed = False

        async def get(self, url, params=None):
            calls_timeout.append((url, dict(params or {})))
            if (params or {}).get("type") == "r":
                return _Response(200, {"results": []})
            raise httpx.ReadTimeout("timed out")

    db_timeout = _DB()
    db_timeout.federal_bond_risk_state.docs.append({
        "_id": quota_id,
        "timestamps": [now - 10.0] * (MAX_REQUESTS_PER_HOUR - 2),
        "quota_revision": 0,
    })
    client_timeout = CourtListenerClient(api_token="test-token")
    client_timeout._in_maintenance_window = lambda: False
    client_timeout._client = _HTTPTimeout()
    res_timeout = await run_federal_bond_risk_scan(
        db_timeout, client=client_timeout, notifier=_Notifier(), pause_s=0, clients=[_client()],
    )
    assert res_timeout["success"] is False
    assert res_timeout["skipped"] == "rate_limited"
    assert res_timeout["rows_written"] == 0
    assert len(calls_timeout) == 2
    assert retry_sleeps == []
    assert len(db_timeout.federal_bond_risk_state.docs[0]["timestamps"]) == MAX_REQUESTS_PER_HOUR

    # 3. Transient 503 with 2 slots remaining reserves attempt 0 + retry attempt 1 before sending,
    #    then leaves rd uncalled rather than exceeding the 50/hr limit.
    calls_503 = []
    retry_sleeps.clear()

    class _HTTP503ThenOK:
        is_closed = False

        async def get(self, url, params=None):
            calls_503.append((url, dict(params or {})))
            if len(calls_503) == 1:
                return _Response(503, {"detail": "unavailable"})
            return _Response(200, {"results": []})

    db_503 = _DB()
    db_503.federal_bond_risk_state.docs.append({
        "_id": quota_id,
        "timestamps": [now - 10.0] * (MAX_REQUESTS_PER_HOUR - 2),
        "quota_revision": 0,
    })
    client_503 = CourtListenerClient(api_token="test-token")
    client_503._in_maintenance_window = lambda: False
    client_503._client = _HTTP503ThenOK()
    res_503 = await run_federal_bond_risk_scan(
        db_503, client=client_503, notifier=_Notifier(), pause_s=0, clients=[_client()],
    )
    assert res_503["success"] is True
    assert res_503["truncated"] is True
    assert res_503["incomplete_coverage"] is False
    assert len(calls_503) == 2
    assert len(retry_sleeps) == 1
    assert len(db_503.federal_bond_risk_state.docs[0]["timestamps"]) == MAX_REQUESTS_PER_HOUR


@pytest.mark.asyncio
async def test_quota_slot_timestamps_record_actual_dispatch_time_after_pacing_and_backoff(monkeypatch):
    import asyncio
    from dashboard.services.federal_bond_risk import (
        DEFAULT_PAUSE_S,
        MAX_REQUESTS_PER_MINUTE,
        _courtlistener_account_key,
        _request_quota_id,
    )

    _enable(monkeypatch)
    clock = [1000.0]
    sleeps = []
    dispatched = []

    class _FakeTime:
        @staticmethod
        def time():
            return clock[0]

    async def fake_sleep(seconds):
        sleeps.append((clock[0], seconds))
        clock[0] += float(seconds)

    monkeypatch.setattr("dashboard.services.federal_bond_risk.time", _FakeTime)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class _Response:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def json(self):
            return self._payload

    class _HTTP:
        is_closed = False

        async def get(self, url, params=None):
            dispatched.append((clock[0], (params or {}).get("type")))
            # First rd call returns 429 with 30s Retry-After; retry returns 200.
            if (params or {}).get("type") == "rd" and len([d for d in dispatched if d[1] == "rd"]) == 1:
                return _Response(429, {"detail": "throttled"}, {"Retry-After": "30"})
            return _Response(200, {"results": []})

    account_key = _courtlistener_account_key(api_token="test-token")
    quota_id = _request_quota_id(account_key)
    db = _DB()
    # Five prior requests 10s ago already fill the rolling 60s window at t=1000.0.
    seed_timestamps = [990.0] * MAX_REQUESTS_PER_MINUTE
    db.federal_bond_risk_state.docs.append({
        "_id": quota_id,
        "timestamps": list(seed_timestamps),
        "quota_revision": 0,
    })

    client = CourtListenerClient(api_token="test-token")
    client._in_maintenance_window = lambda: False
    client._client = _HTTP()

    # Scan 1: r waits 50s for rolling 60s window (1050.0), rd waits 12s (1062.0),
    # then 429 retry waits 30s Retry-After (1092.0).
    scan1 = await run_federal_bond_risk_scan(
        db, client=client, notifier=_Notifier(), pause_s=DEFAULT_PAUSE_S, clients=[_client()],
    )
    assert scan1["success"] is True
    stored_after_scan1 = list(db.federal_bond_risk_state.docs[0]["timestamps"])
    assert stored_after_scan1[-3:] == [pytest.approx(1050.0), pytest.approx(1062.0), pytest.approx(1092.0)]

    # Scan 2 begins 0.1s later (1092.1): must measure from actual retry dispatch (1092.0),
    # sleep ~11.9s until 1104.0, and never fire consecutively.
    clock[0] = 1092.1
    scan2 = await run_federal_bond_risk_scan(
        db, client=client, notifier=_Notifier(), pause_s=DEFAULT_PAUSE_S, clients=[_client()],
    )
    assert scan2["success"] is True

    dispatch_times = [ts for ts, _stype in dispatched]
    assert dispatch_times == [
        pytest.approx(1050.0),
        pytest.approx(1062.0),
        pytest.approx(1092.0),
        pytest.approx(1104.0),
        pytest.approx(1116.0),
    ]
    all_events = sorted(seed_timestamps + dispatch_times)
    for t1, t2 in zip(dispatch_times, dispatch_times[1:]):
        assert t2 - t1 >= DEFAULT_PAUSE_S - 1e-6
    for ts in dispatch_times:
        in_window = [val for val in all_events if ts - 60.0 < val <= ts]
        assert len(in_window) <= MAX_REQUESTS_PER_MINUTE

