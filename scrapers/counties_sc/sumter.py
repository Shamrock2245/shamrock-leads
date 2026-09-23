"""
Sumter County (SC) Arrest Scraper — SmartCOP SmartWebClient.
"""
from scrapers.smartcop_base import SmartCOPBaseScraper


class SumterScraper(SmartCOPBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "SmartCOP path invents booking_number from name+date (synthetic key); source-issued key required."
    )
    @property
    def county(self) -> str:
        return "Sumter"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def portal_url(self) -> str:
        return "https://portal.sumtercountysheriff.org/smartwebclient/jail.aspx"
