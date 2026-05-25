import os
import sys

# Try loading from .env
from dotenv import load_dotenv
load_dotenv()

from curl_cffi import requests

# Targets to try
targets = ["chrome101", "chrome110", "chrome120", "chrome124", "safari_15_6", "edge101"]

url = "https://inmates.charlottecountyfl.revize.com/bookings"

print("Starting curl_cffi bypass tests...")

for target in targets:
    try:
        session = requests.Session()
        # Clean browser headers
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        
        # Test without referrer
        r = session.get(url, headers=headers, impersonate=target, timeout=15)
        print(f"Target: {target:12s} | Referrer: None       | Status: {r.status_code} | Title: {'Just a moment' not in r.text}")
        
        # Test with CCSo referrer
        headers["Sec-Fetch-Site"] = "cross-site"
        headers["Referer"] = "https://ccso.org/correctional_facility/local_arrest_database.php"
        r = session.get(url, headers=headers, impersonate=target, timeout=15)
        print(f"Target: {target:12s} | Referrer: CCSO       | Status: {r.status_code} | Title: {'Just a moment' not in r.text}")
        
    except Exception as e:
        print(f"Target: {target:12s} | Error: {e}")
