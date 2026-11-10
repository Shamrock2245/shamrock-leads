"""Separate OS processes against an isolated local MongoDB.

Skipped unless SHAMROCK_TEST_MONGODB_URI is a loopback mongodb:// URI with
no credentials. A remote or credentialed URI is refused. This file never
reads MONGODB_URI. CI does not set the variable, so these tests skip there.

The production helpers run on real Motor collections. Duplicate _id inserts
use MongoDB's built-in unique _id index and return duplicate key 11000.
"""
from __future__ import annotations

import asyncio
import os
import time
import traceback
from multiprocessing import get_context

import pytest

EMAIL = "admin@shamrockbailbonds.biz"
PHONE = "2395550100"
NAME = "Test Indemnitor"
DOB = "1900-01-01"
DB_NAME = "shamrock_self_serve_mp_test"


def _uri() -> str:
    uri = os.environ.get("SHAMROCK_TEST_MONGODB_URI", "").strip()
    if not uri:
        pytest.skip(
            "SHAMROCK_TEST_MONGODB_URI is unset; isolated MongoDB worker coverage is not run"
        )
    lowered = uri.lower()
    if lowered.startswith("mongodb+srv") or "mongodb.net" in lowered or "@" in uri:
        pytest.fail("refusing a remote or credentialed MongoDB URI")
    if not (
        lowered.startswith("mongodb://127.0.0.1:")
        or lowered.startswith("mongodb://localhost:")
    ):
        pytest.fail("refusing a non-loopback MongoDB URI")
    return uri


def _client(uri: str):
    from pymongo import MongoClient

    return MongoClient(uri, serverSelectionTimeoutMS=4000)


def _drop(uri: str) -> None:
    client = _client(uri)
    try:
        client.drop_database(DB_NAME)
    finally:
        client.close()


def _install_motor(uri: str):
    os.environ["ENV"] = "test"
    os.environ["SECRET_KEY"] = "ci-not-a-real-secret"
    os.environ["DOCUSEAL_TEMPLATE_ID_PALMETTO"] = "505"
    os.environ["DOCUSEAL_TEMPLATE_ID_OSI"] = "101"
    from motor.motor_asyncio import AsyncIOMotorClient
    import dashboard.extensions as extensions
    import dashboard.services.paperwork_self_serve as packets
    import dashboard.services.staff_chain_service as chain

    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=4000)
    database = client[DB_NAME]

    def get_collection(name):
        return database[name]

    extensions.get_collection = get_collection
    chain.get_collection = get_collection
    return client, database


def _run_pair(uri: str, worker):
    start = f"/tmp/mongo-self-serve-go-{os.getpid()}-{time.time_ns()}"
    ctx = get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(uri, start, slot, queue)) for slot in (1, 2)]
    for proc in procs:
        proc.start()
    time.sleep(0.4)
    with open(start, "w", encoding="utf-8") as handle:
        handle.write("go")
    for proc in procs:
        proc.join(40)
    results = []
    while not queue.empty():
        results.append(queue.get())
    codes = [proc.exitcode for proc in procs]
    try:
        os.remove(start)
    except OSError:
        pass
    return results, codes


def _case_insert_worker(uri, start, slot, queue):
    try:
        while not os.path.exists(start):
            time.sleep(0.01)
        _client_motor, database = _install_motor(uri)
        from dashboard.services.exclusive_insert import insert_new
        from pymongo.errors import DuplicateKeyError

        async def _once():
            doc = {
                "_id": "mongo-case-claim",
                "bond_case_id": "mongo-case-claim",
                "source": "self_serve",
            }
            won = await insert_new(database["bond_cases"], doc)
            code = None
            if not won:
                try:
                    await database["bond_cases"].insert_one(dict(doc))
                except DuplicateKeyError as exc:
                    code = exc.code
            return won, code

        won, code = asyncio.run(_once())
        queue.put({"ok": True, "slot": slot, "won": won, "code": code})
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def _packet_insert_worker(uri, start, slot, queue):
    try:
        while not os.path.exists(start):
            time.sleep(0.01)
        _client_motor, database = _install_motor(uri)
        from dashboard.services.exclusive_insert import insert_new
        from pymongo.errors import DuplicateKeyError

        async def _once():
            doc = {
                "_id": "mongo-packet-claim",
                "packet_id": "mongo-packet-claim",
                "status": "building",
                "docuseal_started": False,
                "sign_url": "",
                "source": "self_serve",
            }
            won = await insert_new(database["paperwork_packets"], doc)
            code = None
            if not won:
                try:
                    await database["paperwork_packets"].insert_one(dict(doc))
                except DuplicateKeyError as exc:
                    code = exc.code
            return won, code

        won, code = asyncio.run(_once())
        queue.put({"ok": True, "slot": slot, "won": won, "code": code})
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def _ensure_worker(uri, start, slot, queue):
    try:
        while not os.path.exists(start):
            time.sleep(0.01)
        _install_motor(uri)
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
                "indemnitor_dl": "TEST-DL-0000",
                "indemnitor_dl_state": "FL",
            },
            scan_blob={"scan_id": "SCAN-TEST-MONGO", "full_name": NAME, "dl_state": "FL"},
            actor="auto",
            walk_in_channel="inperson",
        ))
        queue.put({
            "ok": bool(result.get("success")),
            "slot": slot,
            "bond_case_id": result.get("bond_case_id"),
            "error": result.get("error"),
        })
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def _packet_build_worker(uri, start, slot, queue):
    try:
        while not os.path.exists(start):
            time.sleep(0.01)
        _client_motor, database = _install_motor(uri)
        import dashboard.services.docuseal_service as docuseal
        import dashboard.services.identity_verification_service as identity
        from dashboard.services.paperwork_self_serve import build_self_serve_packet

        async def _ok(**kwargs):
            return {"ok": True}

        async def _create(self, **kwargs):
            await database["provider_calls"].insert_one({"slot": slot, "template_id": kwargs.get("template_id")})
            await asyncio.sleep(0.4)
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
        result = asyncio.run(build_self_serve_packet(
            case={
                "bond_case_id": "BC-TEST-MONGO",
                "indemnitor_id": "IND-TEST-MONGO",
                "surety_id": "palmetto",
                "source": "staff",
            },
            role="indemnitor",
            person_id="IND-TEST-MONGO",
            fields={
                "indemnitor_name": NAME,
                "indemnitor_dob": DOB,
                "indemnitor_dl": "TEST-DL-0000",
                "indemnitor_dl_state": "FL",
            },
            email=EMAIL,
            phone=PHONE,
            scan_id="SCAN-TEST-MONGO",
        ))
        queue.put({
            "ok": True,
            "slot": slot,
            "packet_id": result.get("packet_id"),
            "signing_link": result.get("signing_link"),
        })
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def test_two_processes_lose_the_second_case_insert_on_11000():
    uri = _uri()
    _drop(uri)
    try:
        results, codes = _run_pair(uri, _case_insert_worker)
        assert codes == [0, 0], results
        assert len(results) == 2, results
        assert all(item.get("ok") for item in results), results
        winners = [item for item in results if item["won"] is True]
        losers = [item for item in results if item["won"] is False]
        assert len(winners) == 1, results
        assert len(losers) == 1, results
        assert losers[0]["code"] == 11000
        client = _client(uri)
        try:
            assert client[DB_NAME]["bond_cases"].count_documents({}) == 1
        finally:
            client.close()
    finally:
        _drop(uri)


def test_two_processes_lose_the_second_packet_insert_on_11000():
    uri = _uri()
    _drop(uri)
    try:
        results, codes = _run_pair(uri, _packet_insert_worker)
        assert codes == [0, 0], results
        assert all(item.get("ok") for item in results), results
        losers = [item for item in results if item["won"] is False]
        assert len(losers) == 1, results
        assert losers[0]["code"] == 11000
        client = _client(uri)
        try:
            assert client[DB_NAME]["paperwork_packets"].count_documents({}) == 1
        finally:
            client.close()
    finally:
        _drop(uri)


def test_two_processes_create_one_walk_in_case():
    uri = _uri()
    _drop(uri)
    try:
        results, codes = _run_pair(uri, _ensure_worker)
        assert codes == [0, 0], results
        assert all(item.get("ok") for item in results), results
        assert results[0]["bond_case_id"] == results[1]["bond_case_id"]
        client = _client(uri)
        try:
            cases = list(client[DB_NAME]["bond_cases"].find({}))
            assert len(cases) == 1
            assert cases[0]["surety_id"] == "osi"
            assert cases[0]["intake_channel"] == "inperson"
            assert cases[0].get("bond_amount") in (None, "")
            assert cases[0].get("premium") in (None, "")
            assert cases[0].get("poa_number") in (None, "")
            assert client[DB_NAME]["matches"].count_documents({}) == 1
            drafts = list(client[DB_NAME]["audit_events"].find({"event_type": "self_serve_draft_bondcase"}))
            assert len(drafts) == 1
        finally:
            client.close()
    finally:
        _drop(uri)


def test_retry_after_a_partial_case_keeps_one_row():
    uri = _uri()
    _drop(uri)
    try:
        _client_motor, database = _install_motor(uri)
        from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

        async def _both():
            async def _call():
                return await ensure_self_serve_bondcase(
                    phone=PHONE,
                    name=NAME,
                    dob=DOB,
                    role="indemnitor",
                    email=EMAIL,
                    identity={"indemnitor_name": NAME, "indemnitor_dob": DOB},
                    scan_blob={"scan_id": "SCAN-TEST-MONGO", "full_name": NAME, "dl_state": "FL"},
                    actor="auto",
                )

            first = await _call()
            deleted = await database["matches"].delete_many({})
            second = await _call()
            return first, second, deleted.deleted_count

        first, second, deleted_count = asyncio.run(_both())
        assert deleted_count == 1
        assert second["bond_case_id"] == first["bond_case_id"]
        client = _client(uri)
        try:
            assert client[DB_NAME]["bond_cases"].count_documents({}) == 1
            assert client[DB_NAME]["matches"].count_documents({}) == 1
            case = client[DB_NAME]["bond_cases"].find_one({})
            assert not case.get("surety_id")
        finally:
            client.close()
    finally:
        _drop(uri)


def test_two_processes_open_one_packet_and_one_provider_call():
    uri = _uri()
    _drop(uri)
    try:
        results, codes = _run_pair(uri, _packet_build_worker)
        assert codes == [0, 0], results
        assert all(item.get("ok") for item in results), results
        assert results[0]["packet_id"] == results[1]["packet_id"]
        assert results[0]["signing_link"].endswith("/s/TEST-SLUG")
        client = _client(uri)
        try:
            packets = list(client[DB_NAME]["paperwork_packets"].find({}))
            assert len(packets) == 1
            assert packets[0]["surety_id"] == "palmetto"
            assert packets[0]["docuseal_started"] is True
            calls = list(client[DB_NAME]["provider_calls"].find({}))
            assert len(calls) == 1
            assert calls[0]["template_id"] == "505"
        finally:
            client.close()
    finally:
        _drop(uri)


def _compensate_worker(uri, start, slot, queue):
    try:
        while not os.path.exists(start):
            time.sleep(0.01)
        _install_motor(uri)
        from dashboard.services.paperwork_self_serve import _compensate_unsent_packet

        changed = asyncio.run(_compensate_unsent_packet("mongo-packet-claim", "mongo-case-claim"))
        queue.put({"ok": True, "slot": slot, "changed": changed})
    except Exception:
        queue.put({"ok": False, "slot": slot, "trace": traceback.format_exc()})


def test_two_processes_compensate_one_unsent_shell():
    uri = _uri()
    _drop(uri)
    try:
        client = _client(uri)
        try:
            client[DB_NAME]["bond_cases"].insert_one({
                "_id": "mongo-case-claim",
                "bond_case_id": "mongo-case-claim",
                "source": "self_serve",
                "self_serve_saga_owned": True,
                "self_serve_saga_status": "open",
                "surety_id": "palmetto",
            })
            client[DB_NAME]["paperwork_packets"].insert_one({
                "_id": "mongo-packet-claim",
                "packet_id": "mongo-packet-claim",
                "bond_case_id": "mongo-case-claim",
                "saga_owned": True,
                "source": "self_serve",
                "status": "building",
                "docuseal_started": False,
                "sign_url": "",
            })
            client[DB_NAME]["indemnitors"].insert_one({
                "_id": "IND-TEST-MONGO",
                "indemnitor_id": "IND-TEST-MONGO",
                "id_scan_records": [{"scan_id": "SCAN-TEST-MONGO", "success": True}],
            })
        finally:
            client.close()
        results, codes = _run_pair(uri, _compensate_worker)
        assert codes == [0, 0], results
        assert sorted(item["changed"] for item in results) == [False, True]
        client = _client(uri)
        try:
            packet = client[DB_NAME]["paperwork_packets"].find_one({"_id": "mongo-packet-claim"})
            case = client[DB_NAME]["bond_cases"].find_one({"_id": "mongo-case-claim"})
            person = client[DB_NAME]["indemnitors"].find_one({"_id": "IND-TEST-MONGO"})
            assert packet["status"] == "compensated"
            assert packet["docuseal_started"] is False
            assert case["self_serve_saga_status"] == "compensated"
            assert case["surety_id"] == "palmetto"
            assert person["id_scan_records"][0]["scan_id"] == "SCAN-TEST-MONGO"
            assert client[DB_NAME]["audit_events"].count_documents(
                {"event_type": "self_serve_saga_compensated"}
            ) == 2
        finally:
            client.close()
    finally:
        _drop(uri)


def test_started_docuseal_claim_is_not_submitted_again():
    uri = _uri()
    _drop(uri)
    try:
        _client_motor, database = _install_motor(uri)
        import dashboard.services.docuseal_service as docuseal
        import dashboard.services.identity_verification_service as identity
        from dashboard.services.paperwork_self_serve import build_self_serve_packet, self_serve_packet_id

        packet_id = self_serve_packet_id("BC-TEST-MONGO", "indemnitor", "IND-TEST-MONGO", [])
        calls = {"n": 0}

        async def _ok(**kwargs):
            return {"ok": True}

        async def _create(self, **kwargs):
            calls["n"] += 1
            return {"id": 1, "submitters": []}

        async def _run():
            await database["paperwork_packets"].insert_one({
                "_id": packet_id,
                "packet_id": packet_id,
                "bond_case_id": "BC-TEST-MONGO",
                "self_serve_packet_key": "BC-TEST-MONGO|indemnitor|IND-TEST-MONGO",
                "person_id": "IND-TEST-MONGO",
                "role": "indemnitor",
                "status": "building",
                "docuseal_started": True,
                "sign_url": "",
                "source": "self_serve",
                "voided": False,
            })
            await build_self_serve_packet(
                case={
                    "bond_case_id": "BC-TEST-MONGO",
                    "indemnitor_id": "IND-TEST-MONGO",
                    "surety_id": "palmetto",
                },
                role="indemnitor",
                person_id="IND-TEST-MONGO",
                fields={"indemnitor_name": NAME, "indemnitor_dob": DOB},
                email=EMAIL,
                phone=PHONE,
                scan_id="SCAN-TEST-MONGO",
            )

        async def _list(self, **kwargs):
            return {"data": []}

        identity.require_verified_indemnitors = _ok
        docuseal.DocuSealService.create_submission = _create
        docuseal.DocuSealService.list_submitters = _list
        with pytest.raises(RuntimeError, match="recovery_required"):
            asyncio.run(_run())
        assert calls["n"] == 0
        client = _client(uri)
        try:
            assert client[DB_NAME]["paperwork_packets"].count_documents({}) == 1
            stored = client[DB_NAME]["paperwork_packets"].find_one({})
            assert stored["sign_url"] == ""
            assert stored["docuseal_started"] is True
            assert stored["status"] == "recovery_required"
            assert client[DB_NAME]["audit_events"].count_documents(
                {"event_type": "self_serve_saga_recovery_required"}
            ) == 1
        finally:
            client.close()
    finally:
        _drop(uri)
