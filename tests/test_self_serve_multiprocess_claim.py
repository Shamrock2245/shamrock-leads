"""Two OS processes claim one self-serve case and one packet.

The store is SQLite with PRIMARY KEY (collection, id) and BEGIN IMMEDIATE
on writes. A second insert of the same ``_id`` raises IntegrityError, which
the adapter surfaces as DuplicateKeyError code 11000. This is the same
conflict the production code handles for MongoDB's built-in unique ``_id``
index. This file does not start mongod and does not prove Atlas.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import time
import traceback
from multiprocessing import get_context
from pathlib import Path

import pytest

from dashboard.services.exclusive_insert import DuplicateKeyError
from tests._inmem_mongo import FakeCollection, matches

EMAIL = "admin@shamrockbailbonds.biz"
PHONE = "2395550100"
NAME = "Test Indemnitor"
DOB = "1900-01-01"


class SqliteCollection:
    """Process-shared collection. Unique ``_id`` is a SQL primary key."""

    def __init__(self, path: str, name: str):
        self.path = path
        self.name = name

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS docs ("
            "collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, "
            "PRIMARY KEY (collection, id))"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS conflicts (collection TEXT NOT NULL)")
        return conn

    def _load(self, conn):
        rows = []
        for (body,) in conn.execute("SELECT body FROM docs WHERE collection=?", (self.name,)):
            rows.append(json.loads(body))
        return rows

    def _write(self, conn, doc):
        conn.execute(
            "UPDATE docs SET body=? WHERE collection=? AND id=?",
            (json.dumps(doc), self.name, str(doc["_id"])),
        )

    async def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        if "_id" not in stored:
            raise DuplicateKeyError("test insert requires _id")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Hold the write lock so the other process can reach this insert
            # before the row commits. The second INSERT then hits the primary key.
            if self.name in {"bond_cases", "paperwork_packets"}:
                time.sleep(0.6)
            conn.execute(
                "INSERT INTO docs(collection, id, body) VALUES (?, ?, ?)",
                (self.name, str(stored["_id"]), json.dumps(stored)),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            conn.execute("INSERT INTO conflicts(collection) VALUES (?)", (self.name,))
            raise DuplicateKeyError(str(exc)) from exc
        finally:
            conn.close()

    async def find_one(self, flt=None, projection=None, sort=None, **kwargs):
        conn = self._connect()
        try:
            rows = self._load(conn)
        finally:
            conn.close()
        hits = [row for row in rows if matches(row, flt or {})]
        return copy.deepcopy(hits[0]) if hits else None

    def find(self, flt=None, projection=None):
        conn = self._connect()
        try:
            rows = [row for row in self._load(conn) if matches(row, flt or {})]
        finally:
            conn.close()
        return _Cursor(rows)

    async def update_one(self, flt, update, upsert=False):
        helper = FakeCollection()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for doc in self._load(conn):
                if matches(doc, flt or {}):
                    helper._apply(doc, update, inserting=False)
                    self._write(conn, doc)
                    conn.execute("COMMIT")
                    return _Result(1)
            conn.execute("COMMIT")
            return _Result(0)
        finally:
            conn.close()


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._it))
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length=None):
        rows = self._docs if length is None else self._docs[:length]
        return copy.deepcopy(rows)


class _Result:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched


class SqliteDB:
    def __init__(self, path: str):
        self.path = path

    def get_collection(self, name):
        return SqliteCollection(self.path, name)


def _install(path: str):
    os.environ["ENV"] = "test"
    os.environ["SECRET_KEY"] = "ci-not-a-real-secret"
    os.environ["DOCUSEAL_TEMPLATE_ID_PALMETTO"] = "5"
    db = SqliteDB(path)
    import dashboard.extensions as extensions
    import dashboard.services.staff_chain_service as chain

    extensions.get_collection = db.get_collection
    chain.get_collection = db.get_collection
    return db


def _case_worker(path: str, start_file: str, slot: int, queue):
    try:
        while not os.path.exists(start_file):
            time.sleep(0.01)
        _install(path)
        from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

        result = asyncio.run(ensure_self_serve_bondcase(
            phone=PHONE,
            name=NAME,
            dob=DOB,
            role="indemnitor",
            email=EMAIL,
            identity={
                "indemnitor_name": NAME,
                "indemnitor_dob": DOB,
                "indemnitor_address": "10 Test Lane",
                "indemnitor_city": "Testville",
                "indemnitor_state": "FL",
                "indemnitor_zip": "00000",
                "indemnitor_dl": "TEST-DL-0000",
                "indemnitor_dl_state": "FL",
            },
            scan_blob={"scan_id": "SCAN-TEST-MP", "full_name": NAME, "dl_state": "FL"},
            actor="auto",
        ))
        queue.put({
            "ok": bool(result.get("success")),
            "bond_case_id": result.get("bond_case_id"),
            "error": result.get("error"),
            "slot": slot,
        })
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def _packet_worker(path: str, start_file: str, slot: int, queue):
    try:
        while not os.path.exists(start_file):
            time.sleep(0.01)
        _install(path)
        import dashboard.services.docuseal_service as docuseal
        import dashboard.services.identity_verification_service as identity
        from dashboard.services.paperwork_self_serve import build_self_serve_packet

        async def _ok(**kwargs):
            return {"ok": True}

        async def _create(self, **kwargs):
            conn = sqlite3.connect(path, timeout=60, isolation_level=None)
            conn.execute("CREATE TABLE IF NOT EXISTS calls (n INTEGER)")
            conn.execute("INSERT INTO calls(n) VALUES (1)")
            conn.close()
            await asyncio.sleep(0.3)
            return {
                "id": 1,
                "submitters": [
                    {"id": 8, "role": "bondsman", "slug": "TEST-BOND", "email": EMAIL, "status": "pending"},
                    {"id": 9, "role": "indemnitor", "slug": "TEST-SLUG", "email": EMAIL, "status": "pending"},
                ],
            }

        async def _full(self, **kwargs):
            raise AssertionError("full packet builder")

        identity.require_verified_indemnitors = _ok
        docuseal.DocuSealService.create_submission = _create
        docuseal.DocuSealService.create_submission_for_packet = _full
        docuseal.resolve_template_id_for_surety = lambda *_a, **_k: "5"
        result = asyncio.run(build_self_serve_packet(
            case={
                "bond_case_id": "BC-TEST-MP",
                "indemnitor_id": "IND-TEST-MP",
                "surety_id": "palmetto",
                "source": "staff",
            },
            role="indemnitor",
            person_id="IND-TEST-MP",
            fields={
                "indemnitor_name": NAME,
                "indemnitor_dob": DOB,
                "indemnitor_address": "10 Test Lane",
                "indemnitor_dl": "TEST-DL-0000",
                "indemnitor_dl_state": "FL",
            },
            email=EMAIL,
            phone=PHONE,
            scan_id="SCAN-TEST-MP",
        ))
        queue.put({
            "ok": True,
            "slot": slot,
            "packet_id": result.get("packet_id"),
            "signing_link": result.get("signing_link"),
        })
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def _rows(path: str, collection: str):
    conn = sqlite3.connect(path, timeout=60)
    try:
        return [
            json.loads(body)
            for (body,) in conn.execute("SELECT body FROM docs WHERE collection=?", (collection,))
        ]
    finally:
        conn.close()


def _conflicts(path: str, collection: str) -> int:
    conn = sqlite3.connect(path, timeout=60)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE collection=?",
            (collection,),
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _run_pair(tmp_path: Path, worker):
    path = str(tmp_path / "claim.sqlite")
    start = str(tmp_path / "start")
    SqliteCollection(path, "bond_cases")._connect().close()
    ctx = get_context("spawn")
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=worker, args=(path, start, slot, queue))
        for slot in (1, 2)
    ]
    for proc in procs:
        proc.start()
    time.sleep(0.3)
    Path(start).write_text("go")
    for proc in procs:
        proc.join(30)
    results = []
    while not queue.empty():
        results.append(queue.get())
    alive = [proc.exitcode for proc in procs]
    return path, results, alive


def test_two_processes_insert_one_draft_case(tmp_path):
    path, results, codes = _run_pair(tmp_path, _case_worker)
    assert codes == [0, 0], results
    assert len(results) == 2, results
    assert all(item.get("ok") for item in results), results
    assert results[0]["bond_case_id"] == results[1]["bond_case_id"]
    cases = _rows(path, "bond_cases")
    assert len(cases) == 1
    assert cases[0]["_id"] == cases[0]["bond_case_id"]
    assert not cases[0].get("surety_id")
    assert len(_rows(path, "matches")) == 1
    drafts = [
        row for row in _rows(path, "audit_events")
        if row.get("event_type") == "self_serve_draft_bondcase"
    ]
    assert len(drafts) == 1
    assert _conflicts(path, "bond_cases") >= 1
    assert NAME not in json.dumps(drafts)


def test_two_processes_open_one_packet(tmp_path):
    path, results, codes = _run_pair(tmp_path, _packet_worker)
    assert codes == [0, 0], results
    assert len(results) == 2, results
    assert all(item.get("ok") for item in results), results
    assert results[0]["packet_id"] == results[1]["packet_id"]
    assert results[0]["signing_link"].endswith("/s/TEST-SLUG")
    packets = _rows(path, "paperwork_packets")
    assert len(packets) == 1
    assert packets[0]["docuseal_started"] is True
    assert packets[0]["sign_url"].endswith("/s/TEST-SLUG")
    conn = sqlite3.connect(path, timeout=60)
    try:
        calls = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    finally:
        conn.close()
    assert calls == 1
    assert _conflicts(path, "paperwork_packets") >= 1
    assert "swipesimple" not in results[0]["signing_link"].lower()
