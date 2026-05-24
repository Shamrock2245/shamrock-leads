import logging
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug-okaloosa-parse")

url = "https://okaloosacountyjail.myokaloosa.com/inmatelocator/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": url,
}

session = requests.Session()
session.headers.update(headers)

try:
    logger.info("Performing GET request...")
    resp = session.get(url, verify=False, timeout=20)
    
    soup = BeautifulSoup(resp.text, "html.parser")
    hidden_fields = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        n = inp.get("name")
        v = inp.get("value", "")
        if n: hidden_fields[n] = v
        
    post_data = dict(hidden_fields)
    post_data["LastName"] = "A"
    post_data["FirstName"] = ""
    
    submit_btn = soup.find("input", {"type": "submit"}) or soup.find("button", {"type": "submit"})
    if submit_btn and submit_btn.get("name"):
        post_data[submit_btn.get("name")] = submit_btn.get("value", "Search")
        
    logger.info("Performing POST...")
    r_post = session.post(url, data=post_data, verify=False, timeout=30)
    
    soup_post = BeautifulSoup(r_post.text, "html.parser")
    tables = soup_post.find_all("table")
    logger.info(f"Found {len(tables)} tables.")
    
    # Let's inspect Table 7 and Table 8 in detail
    if len(tables) > 8:
        t7 = tables[7]
        t8 = tables[8]
        
        logger.info("--- TABLE 7 (HEADERS) ---")
        t7_rows = t7.find_all("tr")
        logger.info(f"Table 7 has {len(t7_rows)} rows.")
        for idx, row in enumerate(t7_rows):
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            logger.info(f"Row {idx} cells: {cells}")
            
        logger.info("--- TABLE 8 (DATA) ---")
        t8_rows = t8.find_all("tr")
        logger.info(f"Table 8 has {len(t8_rows)} rows.")
        for idx, row in enumerate(t8_rows[:5]):
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            logger.info(f"Row {idx} cells: {cells}")
            
        # Let's print parent elements of Table 7 and 8 to see if they are part of a grid
        logger.info("--- Parent classes/IDs ---")
        logger.info(f"Table 7 parent: tag={t7.parent.name}, id={t7.parent.get('id')}, class={t7.parent.get('class')}")
        logger.info(f"Table 8 parent: tag={t8.parent.name}, id={t8.parent.get('id')}, class={t8.parent.get('class')}")
        
        # Let's see if there is an overarching GridView or similar ASP.NET element
        grid = soup_post.find(id=lambda x: x and "grdSrchResults" in x)
        if grid:
            logger.info(f"Found grid with ID containing grdSrchResults: {grid.name}, id={grid.get('id')}")
            # Print row structures inside grid
            g_rows = grid.find_all("tr", recursive=False) or grid.find_all("tr")
            logger.info(f"Grid has {len(g_rows)} total rows (recursive).")
            # Let's dump first 3 rows text
            for idx, r in enumerate(g_rows[:8]):
                logger.info(f"Grid Row {idx} tag={r.name}, class={r.get('class')}, id={r.get('id')} -> text={r.get_text(' ', strip=True)[:200]}")
                
except Exception as e:
    logger.error(f"Error occurred: {e}")
