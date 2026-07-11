from scrapers.base_scraper import BaseScraper
import logging

class RichlandSCScraper(BaseScraper):
    @property
    def county(self): return "Richland"
    @property
    def state(self): return "SC"
    
    def scrape(self):
        # Implementation to be filled out or rely on base if applicable
        return []
