import logging
import os
import sys
import time
from DrissionPage import ChromiumPage

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from scrapers.base_scraper import BaseScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-hardee-traffic")

class TestHardee(BaseScraper):
    @property
    def county(self): return "Hardee"
    def scrape(self): return []

scraper = TestHardee()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://apps.myocv.com/share/a27833873 ...")
    page.listen.start()  # Listen to all traffic
    page.get("https://apps.myocv.com/share/a27833873")
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
