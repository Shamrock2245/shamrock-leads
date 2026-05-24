"""
Escambia County Arrest Scraper — SmartCOP SmartWeb.
Source: Escambia County Sheriff's Office
URL: https://inmatelookup.myescambia.com/smartwebclient/jail.aspx
Method: requests + BeautifulSoup via shared smartweb_parser
"""
import logging
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.smartweb_parser import scrape_smartweb

logger = logging.getLogger(__name__)

BASE_URL = "https://inmatelookup.myescambia.com"
SEARCH_URL = f"{BASE_URL}/smartwebclient/jail.aspx"
FACILITY = "Escambia County Jail"


class EscambiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Escambia"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
        except ImportError:
            logger.error("requests not installed")
            raise

        session = requests.Session()
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        try:
            logger.info(f"Starting Escambia SmartWeb scrape using base URL: {SEARCH_URL}")
            records = scrape_smartweb(
                base_url=SEARCH_URL,
                county=self.county,
                facility=FACILITY,
                session=session,
                ArrestRecord=ArrestRecord
            )
            logger.info(f"Escambia: {len(records)} records scraped successfully")
            return records
        except Exception as e:
            logger.error(f"Escambia SmartWeb fatal: {e}")
            raise
