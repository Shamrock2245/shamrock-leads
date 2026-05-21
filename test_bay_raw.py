import requests
import re
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.baysomobile.org/is"
session = requests.Session()
session.headers.update(HEADERS)

print("Loading page...")
resp = session.get(f"{BASE_URL}/", timeout=30)
print(f"Initial GET Status: {resp.status_code}")

sid = ""
for pattern in [r'_S_ID["\s]*[:=]["\s]*([A-Za-z0-9]+)', r'"_S_ID":"([^"]+)"', r'_S_ID=([A-Za-z0-9]+)']:
    m = re.search(pattern, resp.text)
    if m:
        sid = m.group(1)
        print(f"Got session ID: {sid}")
        break

if not sid:
    print("Could not extract session ID!")

search_headers = {
    **HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/",
}

post_data = f"Ajax=1&IsEvent=1&Obj=O68&Evt=click&this=O68&_S_ID={sid}&_seq_=3&_uo_=O0"

print("Submitting search...")
search_resp = session.post(
    f"{BASE_URL}/hyb.dll/HandleEvent",
    data=post_data,
    headers=search_headers,
    timeout=30,
)
print(f"POST Status: {search_resp.status_code}")

print("\n--- FIRST 1000 CHARS OF GET RESPONSE ---")
print(resp.text[:1000])

print("\n--- FIRST 1000 CHARS OF POST RESPONSE ---")
print(search_resp.text[:1000])

# Parse table
soup = BeautifulSoup(search_resp.text, "html.parser")
rows = soup.find_all("tr")
print(f"\nFound {len(rows)} tr elements in POST response")

soup_get = BeautifulSoup(resp.text, "html.parser")
rows_get = soup_get.find_all("tr")
print(f"Found {len(rows_get)} tr elements in GET response")
