"""
Walton County (GA) Arrest Scraper.
Uses direct XML feed.
"""

from scrapers.xml_feed_base import XMLFeedBaseScraper

class WaltonScraper(XMLFeedBaseScraper):
    @property
    def county(self) -> str:
        return "Walton"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def feed_url(self) -> str:
        return "https://wcso.waltoncountyga.gov/jailroster.xml"
