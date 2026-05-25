import requests
from bs4 import BeautifulSoup
import urllib3
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://webapps.hcso.tampa.fl.us/arrestinquiry/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": url,
}

session = requests.Session()
session.headers.update(headers)

try:
    print("Step 1: Loading main page to get tokens...")
    resp = session.get(url, verify=False, timeout=15)
    print(f"GET Status: {resp.status_code}")
    
    soup = BeautifulSoup(resp.text, "html.parser")
    token_el = soup.find("input", {"name": "__RequestVerificationToken"})
    if not token_el:
        print("Error: __RequestVerificationToken not found!")
        exit(1)
        
    token = token_el["value"]
    print(f"Extracted token: {token}")
    
    # Try different search criteria to see how it responds
    payloads = [
        # 1. Just current inmates checkbox, no name (empty search)
        {
            "__RequestVerificationToken": token,
            "SearchBookingNumber": "",
            "SearchName": "",
            "SearchBookingDate": "",
            "SearchReleaseDate": "",
            "SearchDOB": "",
            "SearchCurrentInmatesOnly": "true",
            "SearchIncludeDetails": "true",
            "SearchSortType": "rbSortBookDate"
        },
        # 2. Search for common last name start like 'a'
        {
            "__RequestVerificationToken": token,
            "SearchBookingNumber": "",
            "SearchName": "a",
            "SearchBookingDate": "",
            "SearchReleaseDate": "",
            "SearchDOB": "",
            "SearchCurrentInmatesOnly": "true",
            "SearchIncludeDetails": "true",
            "SearchSortType": "rbSortBookDate"
        }
    ]
    
    for idx, payload in enumerate(payloads):
        print(f"\nStep 2: Performing POST search {idx+1}...")
        post_resp = session.post(url, data=payload, verify=False, timeout=20)
        print(f"POST Status: {post_resp.status_code}")
        
        soup_res = BeautifulSoup(post_resp.text, "html.parser")
        
        # Save response for inspection
        os.makedirs("scratch", exist_ok=True)
        filename = f"scratch/hillsborough_search_res_{idx+1}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(post_resp.text)
        print(f"Saved response to {filename}")
        
        # Check if table class table-striped or booking records exist
        tables = soup_res.find_all("table")
        print(f"Found {len(tables)} tables in result.")
        
        body_text = soup_res.body.get_text(" ", strip=True) if soup_res.body else ""
        print(f"Page text snippet: {body_text[:500]}")
        
        # Look for table with class table-striped
        striped = soup_res.find("table", class_="table-striped")
        if striped:
            rows = striped.find_all("tr")
            print(f"Success! Found striped table with {len(rows)} rows.")
            break
        else:
            print("No striped table found in this payload result.")
            
except Exception as e:
    print(f"Error occurred: {e}")
