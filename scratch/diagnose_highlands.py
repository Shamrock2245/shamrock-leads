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
logger = logging.getLogger("diagnose-highlands")

class TestHighlands(BaseScraper):
    @property
    def county(self): return "Highlands"
    def scrape(self): return []

scraper = TestHighlands()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to Highlands inmate search...")
    page.listen.start()  # Listen to all network requests
    page.get("https://www.highlandssheriff.org/inmateSearch")
    time.sleep(10)
    
    # 1. Inspect captured network packets
    packets = page.listen.steps(timeout=5)
    logger.info("Captured network traffic:")
    for idx, p in enumerate(packets):
        logger.info(f"Packet {idx}: URL={p.url}, Method={p.method}")
        if p.response:
            logger.info(f"  Status={p.response.status}")
            try:
                # Print response body if it's JSON or small text
                body = p.response.body
                if isinstance(body, dict):
                    logger.info(f"  Body (JSON keys): {list(body.keys())}")
                elif isinstance(body, str) and (body.startswith("{") or body.startswith("[")):
                    import json
                    logger.info(f"  Body (JSON keys): {list(json.loads(body).keys())}")
                elif isinstance(body, str) and len(body) < 300:
                    logger.info(f"  Body: {body}")
            except:
                pass

    # 2. Inspect rendered HTML structure
    soup = BeautifulSoup(page.html, "html.parser")
    logger.info(f"Page title: {soup.title.text if soup.title else 'None'}")
    
    # Let's search for typical inmate name layouts or text on the page
    text = soup.body.get_text(" ", strip=True) if soup.body else soup.get_text(" ", strip=True)
    logger.info(f"Page text snippet (first 1000 chars): {text[:1000]}")
    
    # Print list of divs/tables/cards
    tables = soup.find_all("table")
    logger.info(f"Found {len(tables)} tables on the page.")
    
    # Print any elements with inmate search or result classes
    logger.info("Searching for card or grid layout elements:")
    for card in soup.find_all(class_=lambda x: x and any(k in str(x).lower() for k in ["card", "inmate", "item", "row", "grid", "record"])):
        logger.info(f"Found match: tag={card.name}, class={card.get('class')}, text={card.get_text(' ', strip=True)[:150]}")
        break  # Just print one to avoid clutter

finally:
    page.listen.stop()
    page.quit()
