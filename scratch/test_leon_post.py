import logging
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-leon-post")

url = "https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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
    
    # Textboxes
    last_name_field = "dnn$ctr633$View$Textbox_633_3"
    first_name_field = "dnn$ctr633$View$Textbox_633_4"
    submit_field = "dnn$ctr633$View$Submitbutton_633_8"
    
    post_data = dict(hidden_fields)
    post_data[last_name_field] = "A"
    post_data[first_name_field] = ""
    post_data[submit_field] = "Search Roster"
    
    logger.info(f"Performing POST with postData keys: {list(post_data.keys())}")
    r_post = session.post(url, data=post_data, verify=False, timeout=30)
    logger.info(f"POST response status: {r_post.status_code}")
    
    soup_post = BeautifulSoup(r_post.text, "html.parser")
    # Check if there are tables
    tables = soup_post.find_all("table")
    logger.info(f"Found {len(tables)} tables in POST response.")
    
    # Print the page text content in summary
    text = soup_post.body.get_text(" ", strip=True) if soup_post.body else soup_post.get_text(" ", strip=True)
    logger.info(f"Page text snippet: {text[:1000]}")
    
except Exception as e:
    logger.error(f"Error occurred: {e}")
