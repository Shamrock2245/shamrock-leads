from __future__ import annotations

from unittest.mock import Mock

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class _UnvalidatedScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = "Fixture source contract is not validated."

    @property
    def county(self) -> str:
        return "Guarded"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> list[ArrestRecord]:
        raise AssertionError("run must stop before scrape()")


def test_unvalidated_source_contract_stops_before_scrape_or_writer():
    scraper = _UnvalidatedScraper()
    writer = Mock()

    result = scraper.run(writers=[writer])

    assert result == {
        "county": "Guarded",
        "records_scraped": 0,
        "elapsed_seconds": 0,
        "source_contract_state": "fail_closed",
        "error": "Fixture source contract is not validated.",
    }
    writer.write_records.assert_not_called()
