import logging
import time
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-leon-browser")

co = ChromiumOptions()
co.auto_port()
co.headless(True)
co.set_argument("--headless=new")
co.set_argument("--no-sandbox")
co.set_argument("--disable-dev-shm-usage")
co.set_argument("--ignore-certificate-errors")
co.set_argument("--ignore-ssl-errors")

page = ChromiumPage(co)

try:
    logger.info("Navigating to Leon inmate search...")
    page.get("https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search")
    page.wait(6)
    
    # Let's find the correct inputs
    last_name_input = page.ele("#dnn_ctr633_View_Textbox_633_3", timeout=5)
    first_name_input = page.ele("#dnn_ctr633_View_Textbox_633_4", timeout=5)
    
    if last_name_input:
        logger.info("Found Last Name input! Leaving it empty...")
        last_name_input.clear()
        time.sleep(0.5)
        
        submit_btn = page.ele("#dnn_ctr633_View_Submitbutton_633_8", timeout=5)
        if submit_btn:
            logger.info("Found Submit button! Clicking...")
            submit_btn.click()
            page.wait(8)
            
            soup = BeautifulSoup(page.html, "html.parser")
            tables = soup.find_all("table")
            logger.info(f"Found {len(tables)} tables in browser response.")
            for idx, t in enumerate(tables):
                logger.info(f"Table {idx} text snippet: {t.get_text(' ', strip=True)[:400]}")
                rows = t.find_all("tr")
                logger.info(f"Table {idx} has {len(rows)} rows.")
                if len(rows) > 1:
                    logger.info(f"Table {idx} first row: {rows[1].get_text(' ', strip=True)}")
        else:
            logger.warning("Could not find submit button!")
    else:
        logger.warning("Could not find Last Name input by ID!")
        
finally:
    page.quit()
