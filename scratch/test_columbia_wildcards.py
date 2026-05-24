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
logger = logging.getLogger("test-columbia-wildcards")

class TestColumbia(BaseScraper):
    @property
    def county(self): return "Columbia"
    def scrape(self): return []

scraper = TestColumbia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

queries = [
    (" ", " "),
    ("*", "*"),
    ("a", "%"),
    ("%", "a"),
    ("a", "*"),
    ("*", "a"),
    ("a", " "),
    (" ", "a"),
]

try:
    for first, last in queries:
        logger.info(f"Navigating to Columbia search for first='{first}', last='{last}'...")
        page.get("https://columbiacountyso.policetocitizen.com/Inmates")
        time.sleep(5)
        
        first_name_input = page.ele("tag:input@@id=First name-input-0", timeout=5) or page.ele("tag:input@@name=First Name", timeout=5)
        last_name_input = page.ele("tag:input@@id=Last name-input-1", timeout=5) or page.ele("tag:input@@name=Last Name", timeout=5)
        
        if not first_name_input or not last_name_input:
            inputs = page.eles("tag:input")
            for inp in inputs:
                html = inp.html.lower()
                if "first" in html: first_name_input = inp
                if "last" in html: last_name_input = inp
                
        if first_name_input and last_name_input:
            first_name_input.input(first)
            time.sleep(0.5)
            last_name_input.input(last)
            time.sleep(0.5)
            
            submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit") or page.ele("tag:button@@class*=-button")
            if submit:
                submit.click()
                time.sleep(6)
                
                soup = BeautifulSoup(page.html, "html.parser")
                text = soup.body.get_text(" ", strip=True)
                
                # Check if "No results found" is in page
                if "no results found" in text.lower():
                    logger.info(f"Query first='{first}', last='{last}': NO RESULTS FOUND.")
                elif "first name is required" in text.lower() or "last name is required" in text.lower():
                    logger.info(f"Query first='{first}', last='{last}': VALIDATION ERROR.")
                else:
                    logger.info(f"Query first='{first}', last='{last}': SUCCESS or different page text!")
                    logger.info(f"Page text: {text[:400]}")
                    tables = soup.find_all("table")
                    logger.info(f"Found {len(tables)} tables.")
                    if tables:
                        break
            else:
                logger.warning("Submit button not found!")
        else:
            logger.warning("Inputs not found!")
            
finally:
    page.quit()
