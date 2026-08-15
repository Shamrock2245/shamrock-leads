from unittest.mock import patch

from scrapers.counties_la.orleans import OrleansScraper
from scrapers.counties_la.st_tammany import StTammanyScraper


def test_orleans_is_explicitly_fail_closed_without_synthetic_booking_fallbacks():
    scraper = OrleansScraper()

    assert scraper.county == "Orleans"
    assert scraper.state == "LA"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.OFFICIAL_SOURCE_URL == "https://www.opso.gov"
    assert "synthetic booking fallbacks" in scraper.SOURCE_CONTRACT_REASON

    with patch("requests.Session") as session:
        assert scraper.scrape() == []
    session.assert_not_called()


def test_st_tammany_is_explicitly_fail_closed_on_blocked_endpoint():
    scraper = StTammanyScraper()

    assert scraper.county == "St. Tammany"
    assert scraper.state == "LA"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.OFFICIAL_SOURCE_URL == "https://www.stpso.com/inmate-search"
    assert "HTTP 403" in scraper.SOURCE_CONTRACT_REASON

    with patch("requests.get") as request_get:
        assert scraper.scrape() == []
    request_get.assert_not_called()
