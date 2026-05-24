import logging
import os
import sys
import time
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from scrapers.base_scraper import BaseScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-escambia-traffic")

class TestEscambia(BaseScraper):
    @property
    def county(self): return "Escambia"
    def scrape(self): return []

scraper = TestEscambia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://www.escambiaso.com/inmate-lookup ...")
    page.listen.start()  # Listen to all traffic
    page.get("https://www.escambiaso.com/inmate-lookup")
    time.sleep(10)
    
    # Let's see what traffic is captured
    packets = page.listen.steps(timeout=10)
    logger.info("Captured network traffic:")
    for p in packets:
        # We only care about json/xhr requests
        if p.url.startswith("chrome") or "google-analytics" in p.url or "fonts." in p.url:
            continue
        logger.info(f"URL: {p.url}")
        if p.response:
            logger.info(f"  Status: {p.response.status}")
            try:
                # If it looks like JSON or contains data
                body = str(p.response.body)
                if body.strip().startswith(("{","[")) or "inmates" in p.url.lower() or "inmate" in p.url.lower():
                    logger.info(f"  Body snippet: {body[:300]}")
            except: pass
            
    # Check DOM structure to see if there are tables or search inputs
    soup = BeautifulSoup(page.html, "html.parser")
    logger.info(f"Page title: {page.title}")
    logger.info(f"Page text snippet: {soup.body.get_text(' ', strip=True)[:1000]}")
    
finally:
    page.listen.stop()
    page.quit()
