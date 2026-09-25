"""TnCIS (TN) must fail closed and never reach Obscura, proxies, or stealth browsers.

Owner decision 2026-09-25: the Cloudflare-protected TnCIS portal has no proven
public source contract, so the Obscura fallback is OFF. A Cloudflare / anti-bot
answer raises AntiBotBlocked (error class ``anti_bot``) with no retry and no
alternate route.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from scrapers import scraper_resilience as sr
from scrapers.base_scraper import BaseScraper
from scrapers.counties import tennessee_tncis_v2_ape as tncis_mod
from scrapers.counties.tennessee_tncis_v2_ape import TennesseeTnCISScraperV2APE, is_anti_bot_response
from scrapers.counties_tn.tncis import TnCISScraper

ROOT = Path(__file__).resolve().parents[1]
LABEL = "TnCIS (TN)"


@pytest.fixture
def scraper():
    return TnCISScraper()


def _fail_obscura(*_a, **_k):
    raise AssertionError("TnCIS must never call Obscura")


def test_tncis_is_fail_closed_in_code_and_registry(scraper):
    from dashboard.extensions import scraper_source_state

    assert scraper.county_label == LABEL
    assert TnCISScraper.SOURCE_CONTRACT_VALIDATED is False
    assert TennesseeTnCISScraperV2APE.SOURCE_CONTRACT_VALIDATED is False
    assert scraper_source_state(LABEL) == "fail_closed"
    assert LABEL in sr.OBSCURA_HARD_DENY_LABELS


def test_run_stops_before_scrape_and_never_touches_obscura(scraper):
    writer = Mock()
    with patch.object(TnCISScraper, "scrape", side_effect=AssertionError("run must stop before scrape()")), \
         patch.object(BaseScraper, "_get_obscura_browser", side_effect=_fail_obscura), \
         patch.object(BaseScraper, "_get_obscura_browser_sync", side_effect=_fail_obscura):
        result = scraper.run(writers=[writer])
    assert result["source_contract_state"] == "fail_closed"
    assert result["records_scraped"] == 0
    writer.write_records.assert_not_called()


def test_scrape_refuses_without_network_or_obscura(scraper):
    with patch("requests.Session") as session_cls, \
         patch.object(BaseScraper, "_get_obscura_browser", side_effect=_fail_obscura), \
         patch.object(BaseScraper, "_get_obscura_browser_sync", side_effect=_fail_obscura), \
         patch.object(BaseScraper, "get_proxy", side_effect=AssertionError("no proxy lookup")):
        with pytest.raises(sr.AntiBotBlocked):
            scraper.scrape()
    session_cls.assert_not_called()


@pytest.mark.parametrize(
    "status,headers,body",
    [
        (403, {"Server": "cloudflare", "CF-RAY": "x"}, "<title>Just a moment...</title>"),
        (503, {"Server": "cloudflare", "cf-ray": "x"}, ""),
        (200, {"cf-mitigated": "challenge"}, ""),
        (200, {}, "<div class='cf-turnstile'></div>"),
    ],
)
def test_cloudflare_answer_is_anti_bot_with_single_direct_request(scraper, status, headers, body):
    """Even if the contract flag were flipped, Cloudflare ⇒ AntiBotBlocked, one request, no fallback."""
    resp = MagicMock(status_code=status, headers=headers, text=body)
    session = MagicMock()
    session.get.return_value = resp
    with patch.object(TnCISScraper, "SOURCE_CONTRACT_VALIDATED", True), \
         patch("requests.Session", return_value=session), \
         patch.object(BaseScraper, "_get_obscura_browser", side_effect=_fail_obscura), \
         patch.object(BaseScraper, "_get_obscura_browser_sync", side_effect=_fail_obscura), \
         patch.object(BaseScraper, "get_proxy", side_effect=AssertionError("no proxy lookup")):
        with pytest.raises(sr.AntiBotBlocked) as exc:
            scraper.scrape()
    assert session.trust_env is False
    assert session.get.call_count == 1
    _args, kwargs = session.get.call_args
    assert "proxies" not in kwargs
    cls = sr.classify_exception(exc.value)
    assert cls.error_class == sr.ERROR_ANTI_BOT
    assert cls.retryable is False


def test_anti_bot_detector_passes_ordinary_page():
    assert is_anti_bot_response(200, {"Server": "nginx"}, "<table><tr><td>x</td></tr></table>") is False
    assert is_anti_bot_response(403, {}, "") is True


def test_obscura_guard_hard_refuses_tncis(scraper):
    with pytest.raises(sr.ObscuraRoutingRefused):
        asyncio.run(scraper._get_obscura_browser())
    with pytest.raises(sr.ObscuraRoutingRefused):
        scraper._get_obscura_browser_sync()
    assert scraper.obscura_route_enabled() is False
    allowed, _ = sr.obscura_route_decision(
        LABEL, source_contract_validated=True, source_state="verified_public", opt_in=frozenset({LABEL})
    )
    assert allowed is False


def test_tncis_module_has_no_obscura_proxy_or_stealth_path():
    src = inspect.getsource(tncis_mod)
    code = src.split('"""', 2)[2]  # skip the module docstring, which documents the removal
    for forbidden in (
        "_get_obscura_browser", "_scrape_with_obscura", "get_proxy", "get_sticky_proxy",
        "s5w2c", "PatchrightBrowserManager", "CurlCFFISession", "stealth_utils", "curl_cffi",
    ):
        assert forbidden not in code, forbidden
    for name in ("_scrape_with_obscura", "_scrape_with_curl_cffi_ape",
                 "_scrape_with_curl_cffi_s5w2c", "_scrape_with_patchright_ape"):
        assert not hasattr(TennesseeTnCISScraperV2APE, name), name


def test_dom_parser_never_emits_rows_without_source_identifier(scraper):
    from bs4 import BeautifulSoup

    html = "<table><tr><td>Doe, John</td><td>no id here</td></tr>" \
           "<tr><td>Roe, Jane</td><td>26-CR-123456</td></tr></table>"
    records = scraper._parse_dom(BeautifulSoup(html, "html.parser"))
    assert [r.Booking_Number for r in records] == ["26-CR-123456"]
