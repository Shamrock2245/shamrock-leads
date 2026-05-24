import logging
import requests
import urllib3
from bs4 import BeautifulSoup
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-okaloosa-parse-logic")

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
    
    html = r_post.text
    soup_post = BeautifulSoup(html, "html.parser")
    
    # EXACT PARSE LOGIC FROM okaloosa.py
    tables = soup_post.find_all("table")
    header_table = None
    header_idx = -1
    for idx, table in enumerate(tables):
        text = table.get_text(" ").lower()
        if "lastname" in text and "firstname" in text and "booking#" in text:
            header_table = table
            header_idx = idx
            break

    logger.info(f"header_idx: {header_idx}")
    if header_table:
        data_table = tables[header_idx + 1]
        logger.info(f"Found data table with {len(data_table.find_all('tr'))} rows.")
        
        header_row = header_table.find("tr")
        headers_list = [th.get_text(strip=True).lower().replace(" ", "").replace("#", "") for th in header_row.find_all(["th", "td"])]
        logger.info(f"Headers: {headers_list}")
        
        def col(name):
            for i, h in enumerate(headers_list):
                if h == name:
                    return i
            return -1

        last_idx = col("lastname")
        first_idx = col("firstname")
        mid_idx = col("middlename")
        booking_idx = col("booking")
        dob_idx = col("dob")
        sex_idx = col("sex")
        race_idx = col("race")
        height_idx = col("height")
        weight_idx = col("weight")
        name_idx = col("name")

        logger.info(f"Indices: last_idx={last_idx}, first_idx={first_idx}, booking_idx={booking_idx}, dob_idx={dob_idx}, sex_idx={sex_idx}")
        
        records = []
        for r_idx, row in enumerate(data_table.find_all("tr")):
            cells = row.find_all(["th", "td"])
            if not cells:
                if r_idx < 5:
                    logger.info(f"Row {r_idx}: no cells")
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]
            if len(cell_texts) < 10:
                if r_idx < 5:
                    logger.info(f"Row {r_idx}: cell count={len(cell_texts)} < 10")
                continue

            def get_val(idx):
                if idx < 0 or idx >= len(cell_texts):
                    return ""
                return cell_texts[idx]

            last_name = get_val(last_idx)
            first_name = get_val(first_idx)
            middle_name = get_val(mid_idx)
            booking_num = get_val(booking_idx)
            
            if r_idx < 5:
                logger.info(f"Row {r_idx} full cell_texts: {cell_texts}")
                logger.info(f"Row {r_idx} mapped values: last_idx={last_idx} -> '{last_name}', first_idx={first_idx} -> '{first_name}', booking_idx={booking_idx} -> '{booking_num}'")
            
            if not last_name or not first_name:
                if r_idx < 5:
                    logger.info(f"Row {r_idx}: skipped because last_name or first_name empty")
                
except Exception as e:
    logger.error(f"Error occurred: {e}")
