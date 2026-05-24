import logging
import requests
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-columbia-prefixes")

url = "https://columbiacountyso.policetocitizen.com/api/Inmates/526"
get_url = "https://columbiacountyso.policetocitizen.com/Inmates"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://columbiacountyso.policetocitizen.com/Inmates/Catalog",
})

# List of prefixes to try
prefixes = [
    # 2-letter first and last name combinations
    ("Jo", "Sm"),
    ("An", "Al"),
    ("Ma", "Ma"),
    ("Da", "Wi"),
    ("Ja", "Jo"),
    ("Ch", "Br"),
    # 3-letter combinations
    ("Joh", "Smi"),
    ("And", "All"),
    ("Mar", "Mar"),
    ("Dav", "Wil"),
    ("Jam", "Joh"),
]

try:
    logger.info("Performing GET request to extract cookies...")
    session.get(get_url, verify=False, timeout=20)
    
    xsrf_token = session.cookies.get("XSRF-TOKEN")
    logger.info(f"Extracted XSRF-TOKEN: {xsrf_token}")
    
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf_token,
        "Referer": "https://columbiacountyso.policetocitizen.com/Inmates/Catalog",
    }
    
    for f, l in prefixes:
        payload = {
            "FilterOptionsParameters": {
                "IntersectionSearch": True,
                "SearchText": "",
                "Parameters": [
                    {"Field": "First Name", "Operation": "equal", "Value": f},
                    {"Field": "Last Name", "Operation": "equal", "Value": l}
                ]
            },
            "IncludeCount": True,
            "PagingOptions": {
                "SortOptions": [
                    {
                        "Name": "ArrestDate",
                        "SortDirection": "Descending",
                        "Sequence": 1
                    }
                ],
                "Take": 50,
                "Skip": 0
            }
        }
        
        logger.info(f"POSTing prefix search first='{f}', last='{l}'...")
        resp = session.post(url, json=payload, headers=headers, verify=False, timeout=25)
        logger.info(f"Response status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("Total", 0)
            inmates = data.get("Inmates", [])
            logger.info(f"  Result: Total={total}, Inmates count={len(inmates)}")
            if inmates:
                logger.info(f"  First inmate: {inmates[0]}")
                break
        else:
            logger.warning(f"  Failed: {resp.text[:300]}")
            
except Exception as e:
    logger.error(f"Error occurred: {e}")
