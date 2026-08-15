from unittest.mock import patch

from scrapers.counties_la.calcasieu import CalcasieuScraper


def test_calcasieu_is_explicitly_fail_closed_on_retired_endpoint():
    scraper = CalcasieuScraper()

    assert scraper.county == "Calcasieu"
    assert scraper.state == "LA"
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.OFFICIAL_SOURCE_URL == "https://www.cpso.com/inmateRoster"
    assert "HTTP 404" in scraper.SOURCE_CONTRACT_REASON

    with patch("requests.get") as request_get:
        assert scraper.scrape() == []
    request_get.assert_not_called()
