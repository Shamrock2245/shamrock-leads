"""Tests for BlueBubbles SMS/RCS fallback and async delivery-failure failover."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.bb_fake_mongo import FakeCollection


@pytest.mark.asyncio
async def test_send_text_any_falls_back_to_sms_guid_then_chat_new():
    from dashboard.routers.bb_private_api import BlueBubblesClient

    client = BlueBubblesClient("http://127.0.0.1:1234", "pw")
    calls = []

    async def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path, dict(json_body or {})))
        if path == "/api/v1/message/text" and json_body.get("chatGuid") == "any;-;+12395550100":
            return {"success": False, "status_code": 500, "message": "Chat does not exist"}
        if path == "/api/v1/message/text" and json_body.get("chatGuid") == "SMS;-;+12395550100":
            return {"success": True, "status_code": 200, "data": {"guid": "sms-msg-1"}}
        return {"success": False, "status_code": 500}

    with patch.object(client, "_consent_gate", AsyncMock(return_value=None)), \
         patch.object(client, "_request", side_effect=fake_request):
        res = await client.send_text(
            "any;-;+12395550100",
            "Hello from Shamrock",
            effect_id="confetti",
            subject="Urgent",
        )

    assert res["success"] is True
    assert res["fallback_channel"] == "sms"
    assert len(calls) == 3
    assert calls[0][2]["method"] == "private-api"
    assert calls[1][2]["method"] == "apple-script"
    assert calls[2][2]["chatGuid"] == "SMS;-;+12395550100"
    assert calls[2][2]["method"] == "apple-script"
    assert "effectId" not in calls[2][2]
    assert "subject" not in calls[2][2]


@pytest.mark.asyncio
async def test_send_text_any_falls_back_to_create_sms_chat_when_sms_guid_missing():
    from dashboard.routers.bb_private_api import BlueBubblesClient

    client = BlueBubblesClient("http://127.0.0.1:1234", "pw")
    calls = []

    async def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path, dict(json_body or {})))
        if path == "/api/v1/message/text":
            return {"success": False, "status_code": 500, "message": "Chat does not exist"}
        if path == "/api/v1/chat/new":
            return {"success": True, "status_code": 200, "data": {"guid": "SMS;-;+12395550101"}}
        return {"success": False, "status_code": 500}

    with patch.object(client, "_consent_gate", AsyncMock(return_value=None)), \
         patch.object(client, "_request", side_effect=fake_request):
        res = await client.send_text("any;-;+12395550101", "New SMS thread")

    assert res["success"] is True
    assert res["fallback_channel"] == "sms"
    assert calls[-1][1] == "/api/v1/chat/new"
    assert calls[-1][2]["service"] == "SMS"
    assert calls[-1][2]["addresses"] == ["+12395550101"]
    assert calls[-1][2]["message"] == "New SMS thread"


@pytest.mark.asyncio
async def test_send_text_explicit_imessage_does_not_downgrade_to_sms():
    from dashboard.routers.bb_private_api import BlueBubblesClient

    client = BlueBubblesClient("http://127.0.0.1:1234", "pw")
    calls = []

    async def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path, dict(json_body or {})))
        return {"success": False, "status_code": 500, "message": "iMessage unavailable"}

    with patch.object(client, "_consent_gate", AsyncMock(return_value=None)), \
         patch.object(client, "_request", side_effect=fake_request):
        res = await client.send_text("iMessage;-;+12395550102", "iMessage only")

    assert res["success"] is False
    assert len(calls) == 2
    assert all(c[1] == "/api/v1/message/text" for c in calls)
    assert all(c[2]["chatGuid"] == "iMessage;-;+12395550102" for c in calls)


@pytest.mark.asyncio
async def test_send_text_blocked_by_consent_never_falls_back():
    from dashboard.routers.bb_private_api import BlueBubblesClient

    client = BlueBubblesClient("http://127.0.0.1:1234", "pw")
    blocked = {"success": False, "blocked": True, "reason": "recipient_opted_out"}
    req_mock = AsyncMock()

    with patch.object(client, "_consent_gate", AsyncMock(return_value=blocked)), \
         patch.object(client, "_request", req_mock):
        res = await client.send_text("any;-;+12395550103", "Should not send")

    assert res["blocked"] is True
    assert req_mock.await_count == 0


@pytest.mark.asyncio
async def test_send_universal_routes_directly_to_sms_when_imessage_unavailable():
    from bson import ObjectId
    from dashboard.services import bb_client

    queue_oid = ObjectId("507f1f77bcf86cd799439011")
    queue_col = FakeCollection("outreach_queue")
    queue_col.docs.append({"_id": queue_oid, "phone": "+12395550104", "status": "pending"})
    fake_bb = MagicMock()
    fake_bb.check_imessage_availability = AsyncMock(return_value={"success": True, "available": False})
    direct_mock = AsyncMock(return_value={
        "success": True,
        "data": {"guid": "bb-guid-123", "chatGuid": "SMS;-;+12395550104"},
    })

    with patch("dashboard.services.agency_billing.suspension_block", AsyncMock(return_value=False)), \
         patch.object(bb_client, "_consent_blocked", AsyncMock(return_value=None)), \
         patch("dashboard.services.outreach_queue.enqueue_message", AsyncMock(return_value=str(queue_oid))), \
         patch.object(bb_client, "get_bb_client", return_value=fake_bb), \
         patch.object(bb_client, "_send_message_direct", direct_mock), \
         patch("dashboard.extensions.get_collection", return_value=queue_col):
        res = await bb_client.send_message_universal("+12395550104", "Court reminder")

    assert res["success"] is True
    assert res["channel"] == "sms"
    assert direct_mock.await_args.kwargs.get("chat_prefix") == "SMS"
    assert queue_col.docs[0]["bb_message_guid"] == "bb-guid-123"
    assert queue_col.docs[0]["chat_guid"] == "SMS;-;+12395550104"
    assert queue_col.docs[0]["channel"] == "sms"


@pytest.mark.asyncio
async def test_webhook_updated_message_error_triggers_idempotent_sms_fallback():
    from dashboard.routers import bb_webhook_receiver

    assert "message-send-error" in bb_webhook_receiver.BB_WEBHOOK_EVENTS

    collections = {
        "imessage_outreach": FakeCollection("imessage_outreach"),
        "outreach_queue": FakeCollection("outreach_queue"),
        "bb_sms_fallback_log": FakeCollection("bb_sms_fallback_log"),
    }

    fake_bb = MagicMock()
    fake_bb.send_text = AsyncMock(return_value={"success": True, "data": {"guid": "sms-fb-guid-1"}})

    event_data = {
        "guid": "imsg-fail-guid-99",
        "text": "Your Shamrock bond paperwork is ready.",
        "isFromMe": True,
        "error": 22,
        "chats": [{"guid": "iMessage;-;+12395550105"}],
        "handle": {"address": "+12395550105"},
    }

    with patch.object(bb_webhook_receiver, "get_collection", side_effect=lambda name: collections.setdefault(name, FakeCollection(name))), \
         patch("dashboard.services.bb_client.get_bb_client", return_value=fake_bb):
        res1 = await bb_webhook_receiver._handle_updated_message(event_data)
        res2 = await bb_webhook_receiver._handle_message_send_error(event_data)

    assert res1["delivery_failed"] is True
    assert res1["sms_fallback"] == "sent"
    assert res1["sms_fallback_guid"] == "sms-fb-guid-1"
    # Second event for the same guid must be deduplicated
    assert res2["sms_fallback"] == "already_attempted"
    assert fake_bb.send_text.await_count == 1
    assert fake_bb.send_text.await_args.args[0] == "SMS;-;+12395550105"
    assert fake_bb.send_text.await_args.kwargs["purpose"] == "sms_fallback"


@pytest.mark.asyncio
async def test_webhook_sms_error_does_not_loop():
    from dashboard.routers import bb_webhook_receiver

    collections = {
        "imessage_outreach": FakeCollection("imessage_outreach"),
        "outreach_queue": FakeCollection("outreach_queue"),
        "bb_sms_fallback_log": FakeCollection("bb_sms_fallback_log"),
    }
    fake_bb = MagicMock()
    fake_bb.send_text = AsyncMock()

    event_data = {
        "guid": "sms-fail-guid-77",
        "text": "Already SMS",
        "isFromMe": True,
        "error": 4,
        "chats": [{"guid": "SMS;-;+12395550106"}],
        "handle": {"address": "+12395550106"},
    }

    with patch.object(bb_webhook_receiver, "get_collection", side_effect=lambda name: collections.setdefault(name, FakeCollection(name))), \
         patch("dashboard.services.bb_client.get_bb_client", return_value=fake_bb):
        res = await bb_webhook_receiver._handle_message_send_error(event_data)

    assert res["delivery_failed"] is True
    assert res["sms_fallback"] == "already_sms"
    assert fake_bb.send_text.await_count == 0
