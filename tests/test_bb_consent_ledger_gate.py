"""Batch A #7: STOP/TCPA consent ledger + per-recipient BlueBubbles send gate.

No real texts: BlueBubblesClient._request is mocked, Mongo is in-memory.
All phone numbers are fictional 555-01xx test numbers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.routers import bb_webhook_receiver as rx
from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.services import sms_consent_ledger as ledger
from dashboard.services.sms_consent_ledger import classify_consent_keyword
from tests.bb_fake_mongo import FakeDB

OPTED = "+12395550111"
OK = "+12395550122"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db():
    fake = FakeDB()
    with patch("dashboard.extensions.get_collection", side_effect=fake.get_collection), \
         patch.object(rx, "get_collection", side_effect=fake.get_collection), \
         patch.object(rx, "get_bb_server", return_value=None), \
         patch.object(rx, "process_inbound", AsyncMock(return_value={"intent": "", "responded": False})) as brain:
        fake.brain = brain
        yield fake


def _inbound(guid, text, phone=OPTED, date_ms=1790000000000):
    return {"guid": guid, "text": text, "isFromMe": False, "handle": {"address": phone},
            "chats": [{"guid": f"any;-;{phone}"}], "dateCreated": date_ms}


def _bb_client():
    client = BlueBubblesClient("http://bb.invalid", "not-a-real-password")
    client._request = AsyncMock(return_value={"success": True, "status_code": 200, "data": {"guid": "SENT"}})
    return client


# ── keyword classification ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,keyword", [
    ("STOP", "stop"), ("Stop.", "stop"), ("stop all", "stopall"), ("STOPALL", "stopall"),
    ("Unsubscribe", "unsubscribe"), ("cancel", "cancel"), ("END", "end"), ("quit!", "quit"),
    ("opt out", "optout"),
])
def test_exact_opt_out_keywords(text, keyword):
    assert classify_consent_keyword(text) == {"event": "opt_out", "keyword": keyword, "match_type": "exact"}


def test_leading_keyword_is_opt_out_flagged_for_review():
    assert classify_consent_keyword("Stop texting me") == {
        "event": "opt_out", "keyword": "stop", "match_type": "leading_keyword"}


@pytest.mark.parametrize("text", ["Quite a day", "Ending soon?", "stopped by the office", "start the paperwork", "hello"])
def test_non_keywords_are_not_consent_events(text):
    assert classify_consent_keyword(text) is None


@pytest.mark.parametrize("text", ["START", "unstop"])
def test_opt_in_keywords(text):
    assert classify_consent_keyword(text)["event"] == "opt_in"


# ── ledger writes from the webhook ─────────────────────────────────────────

def test_inbound_stop_writes_ledger_and_legacy_side_effects(db):
    db["outreach_sequences"].docs.append({"_id": "s1", "phone": OPTED, "status": "active"})
    db["prospective_bonds"].docs.append({"_id": "p1", "indemnitor": {"phone": OPTED}, "status": "active"})
    res = _run(rx._handle_new_message(_inbound("STOP-1", "STOP"), db))
    assert res["opted_out"] is True
    db.brain.assert_not_called()
    (entry,) = db["sms_consent_ledger"].docs
    assert entry["phone"] == OPTED and entry["phone_last10"] == "2395550111"
    assert entry["event"] == "opt_out" and entry["keyword"] == "stop"
    assert entry["source_message_guid"] == "STOP-1"
    assert entry["match_type"] == "exact" and entry["needs_staff_review"] is False
    assert entry["timestamp"].isoformat().startswith("2026-09-21T14:13:20")
    assert db["outreach_sequences"].docs[0]["status"] == "stopped"
    assert db["prospective_bonds"].docs[0]["opted_out"] is True
    # BB re-delivery of the same message is idempotent
    _run(rx._handle_new_message(_inbound("STOP-1", "STOP"), db))
    assert len(db["sms_consent_ledger"].docs) == 1
    assert len([r for r in db["imessage_outreach"].docs if r.get("category") == "opt_out"]) == 1


def test_start_after_stop_re_enables_sends(db):
    _run(rx._handle_new_message(_inbound("S-1", "stop", date_ms=1790000000000), db))
    assert _run(ledger.get_consent_state(OPTED))["opted_out"] is True
    _run(rx._handle_new_message(_inbound("S-2", "START", date_ms=1790000600000), db))
    state = _run(ledger.get_consent_state(OPTED))
    assert state["opted_out"] is False and state["event"]["event"] == "opt_in"


# ── send gate ───────────────────────────────────────────────────────────────

def _opt_out(db, phone=OPTED):
    _run(ledger.record_consent_event(phone=phone, event="opt_out", keyword="stop",
                                     source_message_guid=f"g-{phone}"))


def test_send_text_blocked_for_opted_out_recipient(db, caplog):
    _opt_out(db)
    client = _bb_client()
    with caplog.at_level(logging.WARNING, logger="dashboard.services.sms_consent_ledger"):
        res = _run(client.send_text(f"any;-;{OPTED}", "promo", purpose="outreach"))
    assert res["blocked"] is True and res["success"] is False
    assert res["reason"] == "recipient_opted_out" and res["purpose"] == "outreach"
    assert res["phone_last4"] == "0111"
    client._request.assert_not_called()
    assert "reason=recipient_opted_out purpose=outreach" in caplog.text
    assert "2395550111" not in caplog.text  # PII-safe log


def test_send_human_like_blocked_before_typing_indicator(db):
    _opt_out(db)
    client = _bb_client()
    res = _run(client.send_human_like(f"any;-;{OPTED}", "hi", typing_delay=0))
    assert res["blocked"] is True
    client._request.assert_not_called()


def test_send_text_allowed_for_other_recipients(db):
    _opt_out(db)
    client = _bb_client()
    res = _run(client.send_text(f"any;-;{OK}", "hello"))
    assert res["success"] is True
    client._request.assert_awaited_once()


def test_send_message_universal_blocked_is_never_queued(db):
    from dashboard.services import bb_client as svc

    _opt_out(db)
    with patch("dashboard.services.outreach_queue.enqueue_message", AsyncMock()) as enqueue, \
         patch.object(svc, "get_bb_client") as get_client:
        res = _run(svc.send_message_universal(OPTED, "court reminder", purpose="court_reminder"))
    assert res["blocked"] is True and res["success"] is False and res["queued"] is False
    assert res["purpose"] == "court_reminder"
    assert svc.bb_send_accepted(res) is False
    enqueue.assert_not_called()
    get_client.assert_not_called()


def test_outreach_queue_marks_blocked_terminal(db):
    from dashboard.services import outreach_queue as oq

    db["outreach_queue"].docs.append({"_id": "q1", "phone": OPTED, "message": "m", "status": "pending",
                                       "retries": 0, "next_attempt": None, "created_at": 1})
    blocked = ledger.blocked_send_result("2395550111", "direct")
    fake_col = MagicMock()
    fake_col.find.return_value.sort.return_value = db["outreach_queue"].find({"status": "pending"})
    fake_col.update_one = AsyncMock()
    with patch.object(oq, "get_collection", return_value=fake_col), \
         patch("dashboard.services.bb_client._send_message_direct", AsyncMock(return_value=blocked)):
        out = _run(oq.process_outreach_queue())
    assert out["blocked"] == 1 and out["retried"] == 0
    final = fake_col.update_one.call_args_list[-1].args[1]["$set"]
    assert final["status"] == "blocked" and final["last_error"] == "recipient_opted_out"


def test_legacy_opt_out_log_is_honoured_without_backfill(db):
    db["imessage_outreach"].docs.append({"recipient_phone": OPTED, "message": "STOP", "direction": "inbound",
                                          "category": "opt_out", "status": "opted_out",
                                          "sent_at": "2026-01-01T00:00:00+00:00"})
    blocked = _run(ledger.check_send_allowed(f"iMessage;-;{OPTED[2:]}", "docuseal_signing_link"))
    assert blocked and blocked["consent_source"] == "legacy_outreach_log"
    assert db["sms_consent_ledger"].docs == []  # no writes


def test_gate_fail_open_and_fail_closed_on_lookup_error():
    def boom(_name):
        raise RuntimeError("mongo down")

    with patch("dashboard.extensions.get_collection", side_effect=boom):
        assert _run(ledger.check_send_allowed(OPTED, "x")) is None
        with patch.dict(os.environ, {"BB_OPTOUT_GATE_FAIL_CLOSED": "true"}):
            res = _run(ledger.check_send_allowed(OPTED, "x"))
    assert res["blocked"] is True and res["reason"] == "consent_check_unavailable"


# ── DocuSeal signing-link exception (B3) ────────────────────────────────────

_PACKET = {
    "packet_id": "PKT-GATE",
    "status": "pending_signature",
    "voided": False,
    "docuseal_submission_id": "SUB-1",
    "docuseal_status": "sent",
    "docuseal_submitters": [
        {"role": "indemnitor", "phone": OK[2:], "external_id": "PKT-GATE:indemnitor:0",
         "metadata": {"packet_id": "PKT-GATE", "party_role": "indemnitor"},
         "sign_url": "https://sign.shamrockbailbonds.biz/s/ind"},
        {"role": "coindemnitor", "phone": OPTED[2:], "external_id": "PKT-GATE:indemnitor:1",
         "metadata": {"packet_id": "PKT-GATE", "party_role": "coindemnitor"},
         "sign_url": "https://sign.shamrockbailbonds.biz/s/co"},
    ],
}
_CONFIG = {"enabled": True, "indemnitor_message_template": "Please sign: {signing_link}"}


def test_docuseal_link_allowed_for_non_opted_out_and_blocked_for_opted_out(db):
    from dashboard.services.docuseal_initial_delivery import deliver_initial_docuseal_links

    _opt_out(db)
    client = _bb_client()
    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client", return_value=client):
        outcome = _run(deliver_initial_docuseal_links(packet=_PACKET, config=_CONFIG))
    assert outcome["state"] == "sent" and outcome["sent_count"] == 1
    assert {"role": "indemnitor", "state": "sent", "channel": "imessage"} in outcome["recipients"]
    assert {"role": "coindemnitor", "state": "blocked", "reason": "recipient_opted_out"} in outcome["recipients"]
    client._request.assert_awaited_once()
    body = client._request.call_args.kwargs["json_body"]
    assert body["chatGuid"] == f"iMessage;-;{OK[2:]}"


def test_docuseal_link_all_recipients_allowed_when_nobody_opted_out(db):
    from dashboard.services.docuseal_initial_delivery import deliver_initial_docuseal_links

    client = _bb_client()
    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client", return_value=client):
        outcome = _run(deliver_initial_docuseal_links(packet=_PACKET, config=_CONFIG))
    assert outcome["sent_count"] == 2
    assert client._request.await_count == 2


# ── poller path honours STOP too ────────────────────────────────────────────

def test_poller_stop_writes_ledger_and_skips_agent_brain(db):
    from dashboard.routers import imessage_automation as ia

    bb = MagicMock()
    bb.get_messages = AsyncMock(return_value={"success": True, "data": [
        {"guid": "P-STOP", "text": "Stop", "isFromMe": False, "handle": {"address": OPTED},
         "chats": [{"guid": f"any;-;{OPTED}"}], "dateCreated": 1790000000000},
    ]})
    brain = AsyncMock()
    with patch.object(ia, "_get_bb_client", return_value=bb), \
         patch.object(ia, "get_db", return_value=db), \
         patch.object(ia, "_get_config", AsyncMock(return_value={})), \
         patch.object(ia, "_update_config", AsyncMock()), \
         patch.object(ia, "process_inbound", brain):
        _run(ia._poll_inbox_once())
    brain.assert_not_called()
    (entry,) = db["sms_consent_ledger"].docs
    assert entry["source"] == "bb_poll" and entry["source_message_guid"] == "P-STOP"
