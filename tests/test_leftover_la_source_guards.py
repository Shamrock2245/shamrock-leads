from unittest.mock import Mock, patch

from scrapers.counties_la.ascension import AscensionScraper
from scrapers.counties_la.caddo import CaddoScraper
from scrapers.counties_la.east_baton_rouge import EastBatonRougeScraper
from scrapers.counties_la.jefferson import JeffersonScraper
from scrapers.counties_la.lafayette import LafayetteScraper
from scrapers.counties_la.livingston import LivingstonScraper
from scrapers.counties_la.ouachita import OuachitaScraper


def _assert_no_network_guard(scraper, county, url_fragment, reason_fragment):
    assert scraper.county == county
    assert scraper.state == "LA"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert url_fragment in scraper.OFFICIAL_SOURCE_URL
    assert reason_fragment in scraper.SOURCE_CONTRACT_REASON
    with patch("requests.get") as request_get:
        assert scraper.scrape() == []
    request_get.assert_not_called()


def test_ascension_is_fail_closed_on_speculative_api():
    _assert_no_network_guard(
        AscensionScraper(),
        "Ascension",
        "ascensionso.com",
        "speculative",
    )


def test_caddo_is_fail_closed_on_speculative_api():
    _assert_no_network_guard(
        CaddoScraper(),
        "Caddo",
        "caddosheriff.org",
        "speculative",
    )


def test_livingston_is_fail_closed_on_speculative_api():
    _assert_no_network_guard(
        LivingstonScraper(),
        "Livingston",
        "lpso.org",
        "speculative",
    )


def test_ouachita_is_fail_closed_on_speculative_api():
    _assert_no_network_guard(
        OuachitaScraper(),
        "Ouachita",
        "opso.net",
        "speculative",
    )


def test_lafayette_is_fail_closed_without_captcha_or_tls_bypass():
    scraper = LafayetteScraper()
    _assert_no_network_guard(
        scraper,
        "Lafayette",
        "lafayettesheriff.com",
        "captcha",
    )
    assert "TLS-disabled" in scraper.SOURCE_CONTRACT_REASON
    assert "name-derived booking fallbacks" in scraper.SOURCE_CONTRACT_REASON


def test_leftover_louisiana_jobs_fail_closed_before_run_writers():
    writer = Mock()
    for scraper in (
        EastBatonRougeScraper(),
        JeffersonScraper(),
        LafayetteScraper(),
        AscensionScraper(),
        CaddoScraper(),
        LivingstonScraper(),
        OuachitaScraper(),
    ):
        result = scraper.run(writers=[writer])
        assert result["source_contract_state"] == "fail_closed"
        assert result["records_scraped"] == 0
    writer.write_records.assert_not_called()
