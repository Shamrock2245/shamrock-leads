"""Unit tests for Mem0 service — no live API calls."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from dashboard.services import mem0_service as m0


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("MEMO_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_ENABLED", raising=False)
    monkeypatch.delenv("MEM0_STORE_ON_INBOUND", raising=False)
    yield


def test_normalize_phone_last10():
    assert m0.normalize_phone_digits("+1 (239) 555-1212") == "2395551212"
    assert m0.normalize_phone_digits("2395551212") == "2395551212"
    assert m0.phone_user_id("1-239-555-1212") == "2395551212"


def test_disabled_without_key():
    assert m0.is_enabled() is False


def test_enabled_with_memo_key(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-testkey")
    assert m0.is_enabled() is True


def test_enabled_with_mem0_alias(monkeypatch):
    monkeypatch.setenv("MEM0_API_KEY", "m0-alias")
    assert m0.is_enabled() is True


def test_explicit_disable(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-testkey")
    monkeypatch.setenv("MEM0_ENABLED", "false")
    assert m0.is_enabled() is False


def test_format_memory_block_empty():
    assert m0.format_memory_block([]) == ""
    assert m0.format_memory_block(["", "  "]) == ""


def test_format_memory_block_content():
    block = m0.format_memory_block(["Caller is mother of John", "Prefers office signing"])
    assert "KNOWN FACTS" in block
    assert "mother of John" in block
    assert "do not invent" in block.lower() or "Do not invent" in block or "not invent" in block


def test_redact_ssn():
    assert "[SSN]" in m0.redact_text("My SSN is 123-45-6789 thanks")


def test_parse_memory_list_shapes():
    assert m0._parse_memory_list([{"memory": "fact A"}, {"memory": "fact B"}]) == [
        "fact A",
        "fact B",
    ]
    assert m0._parse_memory_list({"results": [{"memory": "x"}]}) == ["x"]
    assert m0._parse_memory_list(None) == []


@pytest.mark.asyncio
async def test_search_facts_disabled_returns_empty():
    facts = await m0.search_facts("2395551212", "bail")
    assert facts == []


@pytest.mark.asyncio
async def test_search_facts_mocked(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"memory": "Previously called about Jane Doe"}]

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    mock_client.get.return_value = mock_resp

    with patch("dashboard.services.mem0_service.httpx.Client", return_value=mock_client):
        facts = await m0.search_facts("2395551212", "Jane")
    assert any("Jane" in f for f in facts)


@pytest.mark.asyncio
async def test_remember_exchange_mocked(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("dashboard.services.mem0_service.httpx.Client", return_value=mock_client):
        ok = await m0.remember_exchange(
            "2395551212",
            [
                {"role": "user", "content": "I need help bonding my son"},
                {"role": "assistant", "content": "We can help — what's your name?"},
            ],
            booking_number="BK1",
            county="Lee",
            intent="interested",
        )
    assert ok is True
    assert mock_client.post.called


@pytest.mark.asyncio
async def test_remember_swallows_errors(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-test")
    with patch(
        "dashboard.services.mem0_service.httpx.Client",
        side_effect=RuntimeError("network"),
    ):
        ok = await m0.remember_exchange(
            "2395551212",
            [{"role": "user", "content": "hi"}],
        )
    assert ok is False


def test_status_snapshot_no_key_leak(monkeypatch):
    monkeypatch.setenv("MEMO_API_KEY", "m0-supersecret")
    snap = m0.status_snapshot()
    assert snap["configured"] is True
    assert "key_prefix" not in snap
    assert "supersecret" not in str(snap)
    assert "m0-sup" not in str(snap)
    assert snap["user_id_scheme"] == "last10_digits_gas_compatible"
