import requests
from bs4 import BeautifulSoup
import urllib3
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

def check_url(url):
    print(f"\nChecking URL: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, verify=False, timeout=15)
        print(f"Status Code: {resp.status_code}")
        print(f"Actual URL after redirects: {resp.url}")
        print(f"Content Length: {len(resp.text)}")
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Print table/rows or some container text to see the structure
            tables = soup.find_all("table")
            print(f"Found {len(tables)} tables")
            for i, t in enumerate(tables):
                print(f"Table {i} rows:")
                for r in t.find_all("tr")[:10]:
                    cells = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
                    print(f"  {cells}")
            
            # Print any booking table rows
            booking_rows = soup.find_all(text=lambda text: text and "booking" in text.lower())
            print(f"Found 'booking' in text {len(booking_rows)} times")
    except Exception as e:
        print(f"Error fetching: {e}")

if __name__ == "__main__":
    check_url("https://www.ircsheriff.org/booking-details/1725627172")
    check_url("https://www.ircsheriff.org/booking_details/1725627172")
