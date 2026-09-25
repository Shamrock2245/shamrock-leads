"""
Tests for Slack #alerts channel integration in ShamrockLeads.
"""
import pytest
import os
import requests
from unittest.mock import MagicMock, patch

from writers.slack_notifier import SlackNotifier, _mask_pii
from dashboard.services.automation_digest import post_slack, digest_poa_low_stock


def test_slack_notifier_alerts_init_defaults(monkeypatch):
    """Test webhook_alerts resolution order and fallback."""
    # Scenario 1: SLACK_WEBHOOK_ALERTS is set
    monkeypatch.setenv("SLACK_WEBHOOK_ALERTS", "https://hooks.slack.com/services/ALERTS/HOOK")
    monkeypatch.setenv("SLACK_WEBHOOK_ERRORS", "https://hooks.slack.com/services/ERRORS/HOOK")
    monkeypatch.setenv("SLACK_CHANNEL_ALERTS", "#alerts")
    
    notifier = SlackNotifier()
    assert notifier.webhook_alerts == "https://hooks.slack.com/services/ALERTS/HOOK"
    assert notifier.channel_alerts == "#alerts"

    # Scenario 2: SLACK_WEBHOOK_ALERTS is unset, falls back to errors
    monkeypatch.delenv("SLACK_WEBHOOK_ALERTS", raising=False)
    notifier2 = SlackNotifier()
    assert notifier2.webhook_alerts == "https://hooks.slack.com/services/ERRORS/HOOK"

    # Scenario 3: Explicit parameter override
    notifier3 = SlackNotifier(webhook_alerts="https://hooks.slack.com/services/CUSTOM/HOOK", channel_alerts="#custom-alerts")
    assert notifier3.webhook_alerts == "https://hooks.slack.com/services/CUSTOM/HOOK"
    assert notifier3.channel_alerts == "#custom-alerts"


def test_notify_alert_formats_and_masks_pii(monkeypatch):
    """Test notify_alert masks PII and constructs proper blocks."""
    slack_calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        if "hooks.slack.com" in url:
            slack_calls.append({"url": url, "payload": json})
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr("writers.slack_notifier.requests.post", fake_post)

    notifier = SlackNotifier(
        webhook_alerts="https://hooks.slack.com/services/ALERTS/HOOK",
        channel_alerts="#alerts",
    )

    success = notifier.notify_alert(
        title="Unscheduled Client Contact 239-555-1234",
        message="Defendant SSN 123-45-6789 reached via (239) 555-9876",
        level="critical",
        details={"case_id": "CASE-101", "phone": "239-555-0000"},
        source="TestAgent",
    )

    assert success is True
    assert len(slack_calls) == 1
    target_url = slack_calls[0]["url"]
    posted_payload = slack_calls[0]["payload"]
    assert target_url == "https://hooks.slack.com/services/ALERTS/HOOK"
    assert posted_payload.get("channel") == "#alerts"

    blocks = posted_payload.get("blocks", [])
    assert len(blocks) >= 4  # Header, fields, message, details, context

    # Check header
    header_text = blocks[0]["text"]["text"]
    assert "🚨" in header_text
    assert "239-555-1234" not in header_text  # Masked!

    # Check fields
    fields = blocks[1]["fields"]
    assert any("CRITICAL" in f["text"] for f in fields)
    assert any("TestAgent" in f["text"] for f in fields)

    # Check message masking
    msg_text = blocks[2]["text"]["text"]
    assert "123-45-6789" not in msg_text
    assert "***-**-" in msg_text
    assert "239-555-9876" not in msg_text

    # Check detail masking
    detail_text = blocks[3]["text"]["text"]
    assert "239-555-0000" not in detail_text


def test_notify_alert_levels():
    """Verify different log levels get appropriate icons."""
    notifier = SlackNotifier(webhook_alerts="https://fake.url")
    calls = []

    def mock_post(url, payload):
        calls.append(payload)
        return True

    notifier._post = mock_post

    for lvl, expected_emoji in [
        ("critical", "🚨"),
        ("error", "❌"),
        ("warning", "⚠️"),
        ("info", "ℹ️"),
        ("success", "✅"),
    ]:
        notifier.notify_alert(title="Test", message="Test Msg", level=lvl)
        last_call = calls[-1]
        assert expected_emoji in last_call["blocks"][0]["text"]["text"]


def test_notify_watchdog_alert():
    """Verify notify_watchdog_alert formats system failures properly."""
    notifier = SlackNotifier(webhook_alerts="https://fake.url")
    captured = {}

    def mock_post(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return True

    notifier._post = mock_post

    ok = notifier.notify_watchdog_alert(
        failures=["API Health check timed out", "GAS Bridge 502"],
        details={"api_health": False, "gas_bridge": False},
    )

    assert ok is True
    blocks = captured["payload"]["blocks"]
    header = blocks[0]["text"]["text"]
    assert "WATCHDOG ALERT" in header
    msg_block = blocks[2]["text"]["text"]
    assert "API Health check timed out" in msg_block
    assert "GAS Bridge 502" in msg_block


def test_slack_alert_post_failure_never_logs_webhook_url(caplog, monkeypatch):
    """Ensure exceptions never leak Slack webhook secret URLs."""
    secret = "https://hooks.slack.com/services/CONFIDENTIAL/SECRET/TOKEN"

    def boom(url, **kwargs):
        raise requests.ConnectionError(f"Connection failed to {url}")

    monkeypatch.setattr("writers.slack_notifier.requests.post", boom)
    notifier = SlackNotifier(webhook_alerts=secret)

    with caplog.at_level("DEBUG"):
        res = notifier.notify_alert(title="Test", message="Failure test")
        assert res is False

    assert "CONFIDENTIAL" not in caplog.text
    assert "SECRET" not in caplog.text
    assert "TOKEN" not in caplog.text


@pytest.mark.asyncio
async def test_automation_digest_alerts_fallback(monkeypatch):
    """Test post_slack falls back to SLACK_WEBHOOK_ERRORS if SLACK_WEBHOOK_ALERTS missing."""
    monkeypatch.delenv("SLACK_WEBHOOK_ALERTS", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_ERRORS", "https://hooks.slack.com/services/ERRORS/HOOK")

    called_url = None

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def post(self, url, json=None, timeout=None):
            nonlocal called_url
            called_url = url
            resp = MagicMock()
            resp.status_code = 200
            return resp

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    success = await post_slack("POA low stock alert", webhook_env="SLACK_WEBHOOK_ALERTS")
    assert success is True
    assert called_url == "https://hooks.slack.com/services/ERRORS/HOOK"
