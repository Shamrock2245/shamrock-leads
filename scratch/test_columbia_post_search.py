import logging
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-columbia-post-search")

url = "https://columbiacountyso.policetocitizen.com/api/Inmates/526"
get_url = "https://columbiacountyso.policetocitizen.com/Inmates"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://columbiacountyso.policetocitizen.com/Inmates/Catalog",
})

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
    
    test_queries = [
        # space and space
        (" ", " "),
        # a and space
        ("a", " "),
        # space and a
        (" ", "a"),
        # a and a
        ("a", "a"),
        # a and b
        ("a", "b"),
        # empty and empty
        ("", ""),
        # None/null fields
        (None, None),
    ]
    
    for f, l in test_queries:
        params = []
        if f is not None:
            params.append({"Field": "First Name", "Operation": "equal", "Value": f})
        if l is not None:
            params.append({"Field": "Last Name", "Operation": "equal", "Value": l})
            
        payload = {
            "FilterOptionsParameters": {
                "IntersectionSearch": True,
                "SearchText": "",
                "Parameters": params
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
        
        logger.info(f"POSTing query first='{f}', last='{l}'...")
        resp = session.post(url, json=payload, headers=headers, verify=False, timeout=25)
        logger.info(f"Response status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("Total", 0)
            inmates = data.get("Inmates", [])
            logger.info(f"  Result: Total={total}, Inmates count={len(inmates)}")
            if inmates:
                logger.info(f"  First inmate snippet: {inmates[0]}")
                break
        else:
            logger.warning(f"  Failed: {resp.text[:300]}")
            
except Exception as e:
    logger.error(f"Error occurred: {e}")
