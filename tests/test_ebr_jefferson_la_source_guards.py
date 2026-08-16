from unittest.mock import patch

from scrapers.counties_la.east_baton_rouge import EastBatonRougeScraper
from scrapers.counties_la.jefferson import JeffersonScraper


def test_east_baton_rouge_is_explicitly_fail_closed_without_stealth_or_hashes():
    scraper = EastBatonRougeScraper()

    assert scraper.county == "East Baton Rouge"
    assert scraper.state == "LA"
    assert scraper.scraper_id == "scraper_la_east_baton_rouge"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.OFFICIAL_SOURCE_URL == "https://www.ebrso.org/resources/prison-inmate-list/"
    assert "name-derived booking fallbacks" in scraper.SOURCE_CONTRACT_REASON
    assert "stealth" in scraper.SOURCE_CONTRACT_REASON

    with patch("requests.get") as request_get:
        assert scraper.scrape() == []
    request_get.assert_not_called()


def test_jefferson_parish_is_explicitly_fail_closed_without_stealth_or_hashes():
    scraper = JeffersonScraper()

    assert scraper.county == "Jefferson"
    assert scraper.state == "LA"
    assert scraper.scraper_id == "scraper_la_jefferson"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.OFFICIAL_SOURCE_URL == "https://apps.jpso.com/inmatesearch/"
    assert "name-derived booking fallbacks" in scraper.SOURCE_CONTRACT_REASON
    assert "stealth" in scraper.SOURCE_CONTRACT_REASON

    with patch("requests.get") as request_get:
        assert scraper.scrape() == []
    request_get.assert_not_called()
