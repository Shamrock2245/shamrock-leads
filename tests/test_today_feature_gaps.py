"""
Tests for 2026-07-28 feature wiring: POA inventory thresholds,
JMS vendor headers, Sherlock CSV true/yes exists.

SignNow hydration checks were retired with the DocuSeal-only paperwork path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapers.jms_headers import get_vendor_headers, JMS_VENDOR_HEADERS
from dashboard.services.poa_service import check_poa_inventory_thresholds, TIERS, inventory_prefix_query


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


# ── POA inventory thresholds ─────────────────────────────────────────────────

def test_inventory_prefix_query_matches_live_and_receipt_spellings():
    clause = inventory_prefix_query("OSI-P51")
    aliases = clause["$or"][0]["poa_prefix"]["$in"]
    assert "OSI51" in aliases
    assert "OSI-P51" in aliases
    regex = clause["$or"][1]["poa_prefix"]["$regex"]
    assert "OSI51" in regex
    assert "OSI" in regex and "P51" in regex

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
    assert first["prefix"] in {item[1] for item in TIERS["osi"] + TIERS["palmetto"]}
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
