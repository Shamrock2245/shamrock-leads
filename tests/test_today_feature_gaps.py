"""
Tests for 2026-07-28 feature wiring: POA inventory thresholds, SignNow
hydration validation, JMS vendor headers, Sherlock CSV true/yes exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapers.jms_headers import get_vendor_headers, JMS_VENDOR_HEADERS
from dashboard.services.signnow_packet_service import SignNowPacketService
from dashboard.services.poa_service import check_poa_inventory_thresholds, TIERS


# ── JMS vendor headers ───────────────────────────────────────────────────────

def test_get_vendor_headers_known_vendor():
    headers = get_vendor_headers("smartcop")
    assert "User-Agent" in headers
    assert headers["Accept"].startswith("text/html")
    assert headers["Sec-Fetch-Mode"] == "navigate"


def test_get_vendor_headers_unknown_falls_back():
    headers = get_vendor_headers("not-a-real-vendor")
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers


def test_jms_vendor_profiles_cover_core_platforms():
    for vendor in ("smartcop", "jailtracker", "zuercher", "p2c", "odyssey", "kologik"):
        assert vendor in JMS_VENDOR_HEADERS


def test_base_scraper_get_vendor_headers_uses_jms_vendor_attr():
    from scrapers.base_scraper import BaseScraper

    class _Dummy(BaseScraper):
        jms_vendor = "p2c"

        @property
        def county(self):
            return "Test"

        def scrape(self):
            return []

    h = _Dummy().get_vendor_headers()
    assert h.get("X-Requested-With") == "XMLHttpRequest"


# ── SignNow hydration validation ─────────────────────────────────────────────

def test_validate_packet_hydration_phase1_missing():
    result = SignNowPacketService.validate_packet_hydration({}, phase=1)
    assert result["valid"] is False
    assert "defendant_name" in result["missing"]
    assert "indemnitor_phone" in result["missing"]


def test_validate_packet_hydration_phase1_flat_ok():
    result = SignNowPacketService.validate_packet_hydration(
        {
            "defendant_name": "Jane Doe",
            "county": "Lee",
            "indemnitor_name": "John Doe",
            "indemnitor_phone": "2395550100",
            "indemnitor_email": "j@example.com",
        },
        phase=1,
    )
    assert result["valid"] is True
    assert result["missing"] == []
    assert result["warnings"] == []


def test_validate_packet_hydration_nested_intake():
    intake = {
        "defendant": {"name": "Jane Doe", "county": "Collier"},
        "indemnitor": {"name": "John Doe", "phone": "2395550199", "email": ""},
    }
    result = SignNowPacketService.validate_packet_hydration(intake, phase=1)
    assert result["valid"] is True
    assert result["fields"]["county"] == "Collier"
    assert any("indemnitor_email" in w for w in result["warnings"])


def test_validate_packet_hydration_phase2_requires_poa_and_case():
    result = SignNowPacketService.validate_packet_hydration(
        {
            "defendant_name": "Jane Doe",
            "county": "Lee",
            "indemnitor_name": "John Doe",
            "indemnitor_phone": "2395550100",
        },
        phase=2,
        poa_number="OSI3 12345",
    )
    assert result["valid"] is False
    assert "case_number" in result["missing"]
    assert "poa_number" not in result["missing"]


# ── POA inventory thresholds ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_poa_inventory_thresholds_low_stock():
    mock_db = MagicMock()
    mock_inv = AsyncMock()
    # First call low (2), rest high (99)
    mock_inv.count_documents = AsyncMock(side_effect=[2] + [99] * 50)
    mock_db.poa_inventory = mock_inv

    with patch("dashboard.extensions.get_db", return_value=mock_db):
        result = await check_poa_inventory_thresholds(threshold=3, notify=False)

    assert result["threshold"] == 3
    assert "checked_at" in result
    assert len(result["low_stock"]) >= 1
    first = result["low_stock"][0]
    assert first["available"] == 2
    assert first["prefix"] in {p for _, p in TIERS["osi"] + TIERS["palmetto"]}
    assert first["tier"] == first["prefix"]


@pytest.mark.asyncio
async def test_check_poa_inventory_thresholds_notify_uses_digest():
    mock_db = MagicMock()
    mock_inv = AsyncMock()
    mock_inv.count_documents = AsyncMock(return_value=0)
    mock_db.poa_inventory = mock_inv

    with patch("dashboard.extensions.get_db", return_value=mock_db), \
         patch(
             "dashboard.services.automation_digest.digest_poa_low_stock",
             new_callable=AsyncMock,
         ) as mock_digest:
        result = await check_poa_inventory_thresholds(threshold=3, notify=True)

    assert result["low_stock"]  # every tier is 0
    mock_digest.assert_awaited_once()
    args, kwargs = mock_digest.await_args
    assert args[1] == 3  # threshold


# ── Sherlock CSV exists=true ─────────────────────────────────────────────────

def test_parse_sherlock_csv_true_exists(tmp_path):
    import sys
    from pathlib import Path

    worker = Path(__file__).resolve().parents[1] / "osint-worker"
    if str(worker) not in sys.path:
        sys.path.insert(0, str(worker))
    from runners import parse_sherlock_csv

    csv_file = tmp_path / "user.csv"
    csv_file.write_text(
        "username,name,url_main,url_user,exists,http_status,response_time_s\n"
        "user,GitHub,https://github.com,https://github.com/user,True,200,0.4\n"
        "user,X,https://x.com,https://x.com/user,False,404,0.2\n"
        "user,Reddit,https://reddit.com,https://reddit.com/u/user,yes,200,0.3\n"
    )
    accounts = parse_sherlock_csv(str(csv_file))
    platforms = {a["platform"] for a in accounts}
    assert platforms == {"GitHub", "Reddit"}
