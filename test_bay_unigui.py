import requests
import re
import logging
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_bay")

BASE_URL = "https://www.baysomobile.org/is"
HANDLE_URL = f"{BASE_URL}/hyb.dll/HandleEvent"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def run_test():
    session = requests.Session()
    session.headers.update(HEADERS)

    logger.info("Loading initial page...")
    resp = session.get(f"{BASE_URL}/", timeout=30)
    
    sid = resp.headers.get("session_id") or resp.headers.get("Session-ID")
    if not sid:
        for pattern in [r'_S_ID["\s]*[:=]["\s]*([A-Za-z0-9]+)', r'"_S_ID":"([^"]+)"', r'_S_ID=([A-Za-z0-9]+)']:
            m = re.search(pattern, resp.text)
            if m:
                sid = m.group(1)
                logger.info(f"Got session ID from body: {sid}")
                break
    else:
        logger.info(f"Got session ID from header: {sid}")

    if not sid:
        logger.error(f"Could not find session ID! Status: {resp.status_code}")
        logger.error(f"Headers: {dict(resp.headers)}")
        logger.error(f"Cookies: {session.cookies.get_dict()}")
        logger.error(f"Body snippet: {resp.text[:500]}")
        return

    search_headers = {
        **HEADERS,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{BASE_URL}/",
    }

    # Step 1: cinfo
    logger.info("Sending cinfo...")
    cinfo_data = f"Ajax=1&IsEvent=1&Obj=O0&Evt=cinfo&ci=br%3D33%3Bos%3D2%3Bbv%3D148%3Bww%3D1605%3Bwh%3D910&_S_ID={sid}&_seq_=0&_uo_=O0"
    r = session.post(HANDLE_URL, data=cinfo_data, headers=search_headers)
    logger.info(f"cinfo status: {r.status_code}, response: {r.text}")

    # Step 2: afterrender
    logger.info("Sending afterrender...")
    afterrender_data = f"Ajax=1&IsEvent=1&Obj=O8&Evt=afterrender&this=O8&_S_ID={sid}&_seq_=1&_uo_=O0"
    r = session.post(HANDLE_URL, data=afterrender_data, headers=search_headers)
    logger.info(f"afterrender status: {r.status_code}, response: {r.text}")

    # Step 3: resize
    logger.info("Sending resize...")
    resize_data = f"Ajax=1&IsEvent=1&Obj=OB8&Evt=resize&w%3D1605&h%3D910&_S_ID={sid}&_seq_=2&_a_=1&_uo_=O0"
    r = session.post(HANDLE_URL, data=resize_data, headers=search_headers)
    logger.info(f"resize status: {r.status_code}, response: {r.text}")

    # Step 4: Click with Last Name = "S" (Form Parameter encoded)
    logger.info("Sending click search for 'S'...")
    # Double URL encode the form parameters string
    fp_raw = "&O5C=%020%02%02S"
    fp_encoded = quote(fp_raw)
    logger.info(f"fp_encoded: {fp_encoded}")
    
    click_data = f"Ajax=1&IsEvent=1&Obj=O68&Evt=click&this=O68&_S_ID={sid}&_fp_={fp_encoded}&_seq_=3&_uo_=O0"
    r = session.post(HANDLE_URL, data=click_data, headers=search_headers)
    logger.info(f"click status: {r.status_code}")
    logger.info(f"click response: {r.text}")

    # Step 5: Fetch store data from grid Obj=O25
    logger.info("Fetching grid data from Obj=O25...")
    data_url = f"{BASE_URL}/hyb.dll/HandleEvent?IsEvent=1&Obj=O25&Evt=data&_S_ID={sid}&page=1&start=0&limit=250"
    r_data = session.get(data_url, headers=HEADERS)
    logger.info(f"Grid data status: {r_data.status_code}")
    logger.info(f"Grid data length: {len(r_data.text)}")
    logger.info(f"Grid data response (first 2000 chars): {r_data.text[:2000]}")

if __name__ == "__main__":
    run_test()
