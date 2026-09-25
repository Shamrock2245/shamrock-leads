"""Batch A #2 (multi-source inbound matching) and #5 (isFromMe outbound ingest).

In-memory Mongo stand-in; agent brain, BlueBubbles and SSE are never reached
over the network.  All phone numbers are 555-01xx fictional test numbers.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.routers import bb_webhook_receiver as rx
from dashboard.services.bb_party_matcher import find_party_matches
from tests.bb_fake_mongo import FakeDB

PHONE = "+12395550101"


@pytest.fixture
def env():
    db = FakeDB()

    async def _fake_process_inbound(**kwargs):
        # Real process_inbound logs the inbound row itself; mimic that.
        await db["imessage_outreach"].insert_one({
            "recipient_phone": kwargs["phone"], "message": kwargs["message_text"],
            "bb_message_guid": kwargs["message_guid"], "direction": "inbound",
            "status": "received", "content_hash": kwargs.get("content_hash", ""),
        })
        return {"intent": "question", "responded": False}

    brain = AsyncMock(side_effect=_fake_process_inbound)
    with patch("dashboard.extensions.get_collection", side_effect=db.get_collection), \
         patch.object(rx, "get_collection", side_effect=db.get_collection), \
         patch.object(rx, "get_bb_server", return_value=None), \
         patch.object(rx, "process_inbound", brain), \
         patch.object(rx, "_OUTBOUND_INGEST_DELAY_SECONDS", 0.0):
        yield db, brain


def _msg(guid, text, *, from_me=False, phone=PHONE, date_ms=1790000000000, **extra):
    data = {
        "guid": guid,
        "text": text,
        "isFromMe": from_me,
        "handle": {"address": phone},
        "chats": [{"guid": f"any;-;{phone}"}],
        "dateCreated": date_ms,
    }
    data.update(extra)
    return data


def _run(coro):
    return asyncio.run(coro)


# ── #2 inbound matching ─────────────────────────────────────────────────────

def test_single_active_bond_defendant_match_attaches_without_auto_reply(env):
    db, brain = env
    db["active_bonds"].docs.append({
        "_id": "ab1", "Bond_Case_ID": "BC-1", "booking_number": "B-100", "status": "active",
        "defendant_name": "TEST DEFENDANT", "defendant_phone": "(239) 555-0101",
    })
    res = _run(rx._handle_new_message(_msg("G1", "when is court?"), db))
    assert res["matched"] is True and res["ambiguous"] is False
    brain.assert_not_called()
    (row,) = db["imessage_outreach"].docs
    assert row["booking_number"] == "B-100"
    assert row["contact_name"] == "TEST DEFENDANT"
    assert row["booking_numbers"] == ["B-100"]
    assert row["matches"][0]["collection"] == "active_bonds"
    assert row["matches"][0]["roles"] == ["defendant"]
    assert "_doc" not in row["matches"][0]
    assert not row.get("needs_staff_review")


def test_multi_match_attaches_all_and_flags_staff_review(env):
    db, brain = env
    db["active_bonds"].docs.append({
        "_id": "ab1", "booking_number": "B-100", "status": "active",
        "indemnitor_phone": "2395550101", "indemnitor_name": "IND ONE",
    })
    db["prospective_bonds"].docs.append({
        "_id": "pb1", "booking_number": "B-200", "status": "active",
        "indemnitor": {"phone": "+15555550000", "name": "SOMEONE ELSE"},
        "indemnitors": [{"phone": "239.555.0101", "name": "CO IND"}],
    })
    db["intake_queue"].docs.append({
        "_id": "iq1", "intake_id": "WX-1", "status": "pending",
        "indemnitor_phone": "+1 239 555 0101", "defendant": {"phone": ""},
    })
    res = _run(rx._handle_new_message(_msg("G2", "hi it's me"), db))
    assert res == {"processed": True, "matched": True, "ambiguous": True, "match_count": 3}
    brain.assert_not_called()  # never auto-pick a party / auto-reply when ambiguous
    (row,) = db["imessage_outreach"].docs
    assert row["needs_staff_review"] is True
    assert row["ambiguous_match"] is True
    assert row["staff_review_reason"] == "ambiguous_phone_match"
    assert "booking_number" not in row and "contact_name" not in row
    assert row["booking_numbers"] == ["B-100", "B-200"]
    assert {m["collection"] for m in row["matches"]} == {"active_bonds", "prospective_bonds", "intake_queue"}
    co = next(m for m in row["matches"] if m["collection"] == "prospective_bonds")
    assert co["roles"] == ["co_indemnitor"]


def test_same_person_same_booking_is_one_party_and_keeps_legacy_agent_brain(env):
    db, brain = env
    db["prospective_bonds"].docs.append({
        "_id": "pb1", "booking_number": "B-300", "status": "active",
        "indemnitor": {"phone": PHONE, "name": "IND"}, "defendant_name": "DEF",
    })
    db["active_bonds"].docs.append({
        "_id": "ab1", "booking_number": "b-300", "status": "active",
        "indemnitor_phone": "2395550101", "indemnitors": [{"phone": "2395550101"}],
    })
    res = _run(rx._handle_new_message(_msg("G3", "ok thanks"), db))
    assert res["matched"] is True and "agent_result" in res
    brain.assert_awaited_once()
    assert brain.call_args.kwargs["bond_doc"]["_id"] == "pb1"
    (row,) = db["imessage_outreach"].docs
    assert row["ambiguous_match"] is False and not row.get("needs_staff_review")
    assert row["booking_number"] == "B-300"


def test_prospective_defendant_match_does_not_trigger_agent_brain(env):
    """Agent brain stays limited to the legacy indemnitor.phone match."""
    db, brain = env
    db["prospective_bonds"].docs.append({
        "_id": "pb1", "booking_number": "B-400", "status": "active",
        "indemnitor": {"phone": "+15555550000"}, "defendant_phone": PHONE,
    })
    res = _run(rx._handle_new_message(_msg("G4", "this is the defendant"), db))
    assert res["matched"] is True
    brain.assert_not_called()


def test_closed_bonds_and_digit_collisions_do_not_match(env):
    db, brain = env
    db["active_bonds"].docs.extend([
        {"_id": "x1", "booking_number": "OLD", "status": "exonerated", "defendant_phone": PHONE},
        {"_id": "x2", "booking_number": "COLL", "status": "active", "defendant_phone": "923955501019"},
    ])
    res = _run(rx._handle_new_message(_msg("G5", "hello"), db))
    assert res == {"processed": True, "matched": False}
    assert db["imessage_outreach"].docs[0]["status"] == "unmatched"


def test_intake_promoted_to_bond_is_same_case():
    db = FakeDB()
    db["active_bonds"].docs.append({"_id": "ab", "booking_number": "B-9", "status": "active",
                                     "intake_id": "IN-9", "indemnitor_phone": PHONE})
    db["intake_queue"].docs.append({"_id": "iq", "intake_id": "IN-9", "status": "promoted",
                                     "indemnitor_phone": PHONE})
    res = asyncio.run(find_party_matches(PHONE, get_collection=db.get_collection))
    assert len(res["matches"]) == 2
    assert res["ambiguous"] is False


def test_lifecycle_timeline_reads_booking_numbers():
    from dashboard.routers import lifecycle_timeline as lt

    db = FakeDB()
    db["imessage_outreach"].docs.append({"_id": "m1", "booking_numbers": ["B-1", "B-2"],
                                          "message": "ambiguous inbound", "direction": "inbound",
                                          "sent_at": "2026-09-25T12:00:00+00:00"})
    with patch.object(lt, "get_collection", side_effect=db.get_collection):
        out = asyncio.run(lt.get_lifecycle("B-2"))
    body = out if isinstance(out, dict) else {}
    assert "ambiguous inbound" in str(body)


# ── #5 outbound (isFromMe) ingest ───────────────────────────────────────────

def test_outbound_is_ingested_without_auto_reply_or_consent_side_effects(env):
    db, brain = env
    db["active_bonds"].docs.append({"_id": "ab1", "booking_number": "B-100", "status": "active",
                                     "indemnitor_phone": PHONE, "indemnitor_name": "IND"})
    res = _run(rx._handle_new_message(_msg("OUT-1", "stop", from_me=True), db))
    assert res["processed"] is True and res["direction"] == "outbound"
    brain.assert_not_called()
    (row,) = db["imessage_outreach"].docs
    assert row["direction"] == "outbound" and row["status"] == "sent" and row["unread"] is False
    assert row["recipient_phone"] == PHONE and row["bb_message_guid"] == "OUT-1"
    assert row["booking_number"] == "B-100"
    # Staff typing "stop" must never opt the recipient out
    assert db["sms_consent_ledger"].docs == []
    assert db["outreach_sequences"].docs == []


def test_outbound_dedup_guid_and_content_hash(env):
    db, _ = env
    first = _run(rx._ingest_outbound_message(_msg("OUT-2", "See you at 3", from_me=True)))
    again = _run(rx._ingest_outbound_message(_msg("OUT-2", "See you at 3", from_me=True)))
    reemit = _run(rx._ingest_outbound_message(_msg("OUT-2b", "See you at 3", from_me=True)))
    assert first["processed"] is True
    assert again["reason"] == "already_processed"
    assert reemit["reason"] == "content_hash_duplicate"
    assert len(db["imessage_outreach"].docs) == 1


def test_outbound_dedups_against_crm_logged_send_and_backfills_guid(env):
    db, _ = env
    db["imessage_outreach"].docs.append({
        "_id": "crm1", "recipient_phone": PHONE, "message": "Your court date is Monday",
        "direction": "outbound", "status": "sent", "sent_by": "dashboard", "bb_message_guid": "",
        "sent_at": "2026-09-21T15:00:00+00:00",
    })
    # dateCreated a few seconds after the CRM row
    date_ms = 1790002805000  # 2026-09-21T15:00:05Z
    res = _run(rx._ingest_outbound_message(_msg("OUT-3", "Your court date is Monday", from_me=True, date_ms=date_ms)))
    assert res["reason"] == "crm_logged_duplicate"
    assert len(db["imessage_outreach"].docs) == 1
    assert db["imessage_outreach"].docs[0]["bb_message_guid"] == "OUT-3"


def test_outbound_dedups_on_temp_guid(env):
    db, _ = env
    db["imessage_outreach"].docs.append({"_id": "crm2", "temp_guid": "shamrock-abc", "recipient_phone": PHONE,
                                          "direction": "outbound", "message": "x"})
    res = _run(rx._ingest_outbound_message(_msg("OUT-4", "different text", from_me=True, tempGuid="shamrock-abc")))
    assert res["reason"] == "temp_guid_duplicate"
    assert db["imessage_outreach"].docs[0]["bb_message_guid"] == "OUT-4"


def test_poller_then_webhook_inbound_does_not_double_insert(env):
    db, _ = env
    first = _run(rx._handle_new_message(_msg("IN-7", "hello there"), db))
    second = _run(rx._handle_new_message(_msg("IN-7", "hello there"), db))
    assert first["processed"] is True
    assert second["reason"] == "already_processed"
    assert len(db["imessage_outreach"].docs) == 1


def test_outbound_deferred_path_schedules_and_ingests(env):
    db, brain = env

    async def scenario():
        with patch.object(rx, "_OUTBOUND_INGEST_DELAY_SECONDS", 0.01):
            res = await rx._handle_new_message(_msg("OUT-5", "on my way", from_me=True), db)
            assert res == {"processed": True, "direction": "outbound", "deferred": True}
            await asyncio.gather(*list(rx._pending_outbound_tasks))

    _run(scenario())
    brain.assert_not_called()
    assert db["imessage_outreach"].docs[0]["direction"] == "outbound"


def test_missing_direction_is_still_skipped(env):
    db, brain = env
    data = _msg("X-1", "hello")
    data.pop("isFromMe")
    res = _run(rx._handle_new_message(data, db))
    assert res["reason"] == "outbound_message_skipped"
    assert db["imessage_outreach"].docs == []
    brain.assert_not_called()
