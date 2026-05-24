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
logger = logging.getLogger("test-columbia-letters")

class TestColumbia(BaseScraper):
    @property
    def county(self): return "Columbia"
    def scrape(self): return []

scraper = TestColumbia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

# Common starting letters
first_letters = ['J', 'A', 'M', 'S', 'R', 'C', 'D', 'K']
last_letters = ['S', 'M', 'B', 'H', 'C', 'W', 'P', 'G', 'D', 'L', 'R', 'T']

try:
    found_any = False
    for f_let in first_letters:
        for l_let in last_letters:
            logger.info(f"Columbia: searching first='{f_let}', last='{l_let}'...")
            page.get("https://columbiacountyso.policetocitizen.com/Inmates")
            time.sleep(4)
            
            first_name_input = page.ele("tag:input@@id=First name-input-0", timeout=5) or page.ele("tag:input@@name=First Name", timeout=5)
            last_name_input = page.ele("tag:input@@id=Last name-input-1", timeout=5) or page.ele("tag:input@@name=Last Name", timeout=5)
            
            if not first_name_input or not last_name_input:
                inputs = page.eles("tag:input")
                for inp in inputs:
                    html = inp.html.lower()
                    if "first" in html: first_name_input = inp
                    if "last" in html: last_name_input = inp
            
            if first_name_input and last_name_input:
                first_name_input.input(f_let)
                time.sleep(0.3)
                last_name_input.input(l_let)
                time.sleep(0.3)
                
                submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit") or page.ele("tag:button@@class*=-button")
                if submit:
                    submit.click()
                    time.sleep(5)
                    
                    soup = BeautifulSoup(page.html, "html.parser")
                    text = soup.body.get_text(" ", strip=True).lower()
                    
                    if "no results found" in text:
                        logger.info(f"  first='{f_let}', last='{l_let}': NO RESULTS FOUND.")
                    elif "required" in text:
                        logger.warning(f"  first='{f_let}', last='{l_let}': VALIDATION ERROR.")
                    else:
                        tables = soup.find_all("table")
                        logger.info(f"  first='{f_let}', last='{l_let}': SUCCESS! Found {len(tables)} tables.")
                        if len(tables) > 0:
                            for idx, t in enumerate(tables):
                                logger.info(f"    Table {idx} rows: {len(t.find_all('tr'))}")
                            found_any = True
                            break
                else:
                    logger.warning("Submit button not found!")
                    break
            else:
                logger.warning("Inputs not found!")
                break
        if found_any:
            break
            
finally:
    page.quit()
