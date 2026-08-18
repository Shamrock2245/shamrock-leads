import asyncio
from unittest.mock import AsyncMock, patch

from dashboard.services.docuseal_initial_delivery import deliver_initial_docuseal_links


_PACKET = {
    "packet_id": "PKT-AUTO-DELIVERY",
    "status": "pending_signature",
    "voided": False,
    "docuseal_submission_id": "SUB-1",
    "docuseal_status": "sent",
    "docuseal_submitters": [
        {
            "role": "indemnitor",
            "phone": "2395550101",
            "external_id": "PKT-AUTO-DELIVERY:indemnitor:0",
            "metadata": {"packet_id": "PKT-AUTO-DELIVERY", "party_role": "indemnitor"},
            "sign_url": "https://sign.shamrockbailbonds.biz/s/indemnitor",
        },
        {
            "role": "defendant",
            "phone": "2395550102",
            "external_id": "PKT-AUTO-DELIVERY:defendant",
            "metadata": {"packet_id": "PKT-AUTO-DELIVERY", "party_role": "defendant"},
            "sign_url": "https://sign.shamrockbailbonds.biz/s/defendant",
        },
    ],
}


def _run(coro):
    return asyncio.run(coro)


def test_initial_delivery_is_disabled_without_explicit_activation():
    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client") as get_client:
        outcome = _run(deliver_initial_docuseal_links(packet=_PACKET, config={"enabled": False}))

    assert outcome["state"] == "blocked"
    assert outcome["reason"] == "disabled"
    get_client.assert_not_called()


def test_initial_delivery_sends_only_bound_indemnitor_with_approved_template():
    client = type("Client", (), {"send_text": AsyncMock(return_value={"success": True})})()
    config = {
        "enabled": True,
        "indemnitor_message_template": "Please sign: {signing_link}",
        "defendant_message_template": "",
        "include_defendant": False,
    }

    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client", return_value=client) as get_client:
        outcome = _run(deliver_initial_docuseal_links(packet=_PACKET, config=config))

    assert outcome["state"] == "sent"
    assert outcome["sent_count"] == 1
    assert outcome["recipients"] == [{"role": "indemnitor", "state": "sent", "channel": "imessage"}]
    get_client.assert_called_once_with("2395550101")
    client.send_text.assert_awaited_once()
    chat_guid, message = client.send_text.call_args.args
    assert chat_guid == "iMessage;-;2395550101"
    assert message == "Please sign: https://sign.shamrockbailbonds.biz/s/indemnitor"


def test_initial_delivery_blocks_defendant_without_separately_approved_copy():
    client = type("Client", (), {"send_text": AsyncMock(return_value={"success": True})})()
    config = {
        "enabled": True,
        "indemnitor_message_template": "Please sign: {signing_link}",
        "defendant_message_template": "",
        "include_defendant": True,
    }

    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client", return_value=client):
        outcome = _run(deliver_initial_docuseal_links(packet=_PACKET, config=config))

    assert outcome["sent_count"] == 1
    assert {row["role"] for row in outcome["recipients"]} == {"indemnitor", "defendant"}
    assert any(
        row == {"role": "defendant", "state": "blocked", "reason": "approved_template_required"}
        for row in outcome["recipients"]
    )
    client.send_text.assert_awaited_once()


def test_initial_delivery_rejects_unbound_or_voided_packets():
    client = type("Client", (), {"send_text": AsyncMock(return_value={"success": True})})()
    unbound = {**_PACKET, "docuseal_submitters": [{**_PACKET["docuseal_submitters"][0], "external_id": "OTHER:indemnitor:0"}]}
    config = {"enabled": True, "indemnitor_message_template": "Please sign: {signing_link}"}

    with patch("dashboard.services.docuseal_initial_delivery.get_bb_client", return_value=client):
        unbound_outcome = _run(deliver_initial_docuseal_links(packet=unbound, config=config))
        voided_outcome = _run(deliver_initial_docuseal_links(packet={**_PACKET, "voided": True}, config=config))

    assert unbound_outcome["state"] == "blocked"
    assert unbound_outcome["reason"] == "no_bound_recipients"
    assert voided_outcome["state"] == "blocked"
    assert voided_outcome["reason"] == "packet_not_eligible"
    client.send_text.assert_not_awaited()
