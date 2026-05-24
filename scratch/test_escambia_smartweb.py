import logging
import re
import time
import urllib3
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-escambia-smartweb")

BASE_URL = "https://inmatelookup.myescambia.com"
SEARCH_URL = f"{BASE_URL}/smartwebclient/jail.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}

session = requests.Session()
session.headers.update(HEADERS)

try:
    logger.info(f"GET search page: {SEARCH_URL} ...")
    resp = session.get(SEARCH_URL, verify=False, timeout=30)
    resp.raise_for_status()
    logger.info("Successfully fetched main page!")
    
    soup = BeautifulSoup(resp.text, "html.parser")
    
    def _get_hidden(name):
        el = soup.find("input", {"name": name})
        return el["value"] if el and el.get("value") else ""
        
    post_data = {
        "__VIEWSTATE": _get_hidden("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _get_hidden("__EVENTVALIDATION"),
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
    }
    
    # Let's find inputs/buttons
    submits = soup.find_all("input", {"type": "submit"})
    logger.info(f"Found {len(submits)} submit buttons:")
    for btn in submits:
        logger.info(f"Submit: name='{btn.get('name')}', value='{btn.get('value')}'")
        
    # Standard smartweb view all button name is usually like 'btnSearch' or 'btnAll' or similar
    # In Dixie it searches for "search", "view", "all"
    btn_to_use = None
    for btn in submits:
        name = btn.get("name", "")
        value = btn.get("value", "")
        if any(kw in value.lower() or kw in name.lower() for kw in ["search", "view", "all", "find", "show"]):
            btn_to_use = btn
            break
            
    if btn_to_use:
        logger.info(f"Selected button: {btn_to_use.get('name')}={btn_to_use.get('value')}")
        post_data[btn_to_use.get("name")] = btn_to_use.get("value")
    else:
        # Fallback if no specific button is found
        if submits:
            btn_to_use = submits[0]
            logger.info(f"Fallback selected button: {btn_to_use.get('name')}={btn_to_use.get('value')}")
            post_data[btn_to_use.get("name")] = btn_to_use.get("value")
            
    # Try POST
    logger.info("Sending POST request to fetch all inmates...")
    resp2 = session.post(SEARCH_URL, data=post_data, verify=False, timeout=60)
    resp2.raise_for_status()
    logger.info("Successfully fetched POST search results!")
    
    soup2 = BeautifulSoup(resp2.text, "html.parser")
    
    # Parse table
    tables = soup2.find_all("table")
    logger.info(f"Found {len(tables)} tables in POST response.")
    
    records_count = 0
    for idx, t in enumerate(tables):
        rows = t.find_all("tr")
        logger.info(f"Table {idx} has {len(rows)} rows.")
        if len(rows) > 1:
            # Check row headings
            header_text = rows[0].get_text(" ").lower()
            logger.info(f"Table {idx} headers: {header_text}")
            if any(kw in header_text for kw in ["name", "booking", "inmate", "arrest"]):
                logger.info(f"Table {idx} matches inmate table structure!")
                for r_idx, r in enumerate(rows[1:10]):
                    cells = r.find_all("td")
                    texts = [c.get_text(strip=True) for c in cells]
                    logger.info(f"  Row {r_idx}: {texts[:5]}")
                    records_count += 1
                break
                
    logger.info(f"Total parsed sample rows: {records_count}")

except Exception as e:
    logger.error(f"Error occurred: {e}")
