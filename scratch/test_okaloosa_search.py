import logging
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-okaloosa-search")

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
    logger.info(f"GET response status: {resp.status_code}")
    
    soup = BeautifulSoup(resp.text, "html.parser")
    hidden_fields = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        n = inp.get("name")
        v = inp.get("value", "")
        if n: hidden_fields[n] = v
        
    logger.info(f"Hidden fields: {list(hidden_fields.keys())}")
    
    post_data = dict(hidden_fields)
    post_data["LastName"] = "A"
    post_data["FirstName"] = ""
    
    # Find the submit button name
    submit_btn = soup.find("input", {"type": "submit"}) or soup.find("button", {"type": "submit"})
    if submit_btn and submit_btn.get("name"):
        post_data[submit_btn.get("name")] = submit_btn.get("value", "Search")
        
    logger.info(f"Performing POST with Last Name = 'A'...")
    r_post = session.post(url, data=post_data, verify=False, timeout=30)
    logger.info(f"POST response status: {r_post.status_code}")
    
    soup_post = BeautifulSoup(r_post.text, "html.parser")
    tables = soup_post.find_all("table")
    logger.info(f"Found {len(tables)} tables in POST response.")
    for idx, t in enumerate(tables):
        logger.info(f"Table {idx} text snippet: {t.get_text(' ', strip=True)[:400]}")
        
except Exception as e:
    logger.error(f"Error occurred: {e}")
