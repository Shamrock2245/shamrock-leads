import logging
import os
import sys
import time
from DrissionPage import ChromiumPage

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from scrapers.base_scraper import BaseScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-highlands-traffic")

class TestHighlands(BaseScraper):
    @property
    def county(self): return "Highlands"
    def scrape(self): return []

scraper = TestHighlands()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://www.highlandssheriff.org/inmateSearch ...")
    page.listen.start()  # Listen to all traffic
    page.get("https://www.highlandssheriff.org/inmateSearch")
    time.sleep(12)
    
    logger.info(f"Page Title: {page.title}")
    logger.info("Captured network traffic:")
    packets = page.listen.steps(timeout=5)
    for p in packets:
        logger.info(f"URL: {p.url}")
        if p.response:
            logger.info(f"  Status: {p.response.status}")
            try:
                body = str(p.response.body)
                if "inmate" in p.url.lower() or "search" in p.url.lower() or body.strip().startswith(("{", "[")):
                    logger.info(f"  Body snippet: {body[:300]}")
            except: pass
            
finally:
    page.listen.stop()
    page.quit()
