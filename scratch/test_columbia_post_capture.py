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
logger = logging.getLogger("test-columbia-post-capture")

class TestColumbia(BaseScraper):
    @property
    def county(self): return "Columbia"
    def scrape(self): return []

scraper = TestColumbia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://columbiacountyso.policetocitizen.com/Inmates ...")
    page.listen.start()  # Listen to all traffic
    page.get("https://columbiacountyso.policetocitizen.com/Inmates")
    time.sleep(8)
    
    # Enter 'a' in both
    first_name_input = page.ele("tag:input@@id=First name-input-0", timeout=5) or page.ele("tag:input@@name=First Name", timeout=5)
    last_name_input = page.ele("tag:input@@id=Last name-input-1", timeout=5) or page.ele("tag:input@@name=Last Name", timeout=5)
    
    if not first_name_input or not last_name_input:
        inputs = page.eles("tag:input")
        for inp in inputs:
            html = inp.html.lower()
            if "first" in html: first_name_input = inp
            if "last" in html: last_name_input = inp
            
    if first_name_input and last_name_input:
        first_name_input.input("a")
        time.sleep(0.5)
        last_name_input.input("a")
        time.sleep(0.5)
        
        submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit") or page.ele("tag:button@@class*=-button")
        if submit:
            logger.info("Clicking Submit...")
            submit.click()
            time.sleep(10)
            
            # Print traffic
            packets = page.listen.steps(timeout=5)
            logger.info("Captured network traffic during search:")
            for p in packets:
                if "/api/" in p.url:
                    logger.info(f"URL: {p.url}")
                    logger.info(f"  Method: {p.method}")
                    if hasattr(p, 'request') and p.request:
                        logger.info(f"  Headers: {p.request.headers}")
                        if hasattr(p.request, 'postData') and p.request.postData:
                            logger.info(f"  PostData: {p.request.postData}")
                    if p.response:
                        logger.info(f"  Status: {p.response.status}")
                        try:
                            logger.info(f"  Body: {p.response.body}")
                        except Exception as e:
                            logger.info(f"  Error reading body: {e}")
        else:
            logger.warning("Submit button not found!")
    else:
        logger.warning("Inputs not found!")
        
finally:
    page.listen.stop()
    page.quit()
