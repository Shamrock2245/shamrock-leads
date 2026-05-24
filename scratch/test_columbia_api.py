import logging
import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-columbia-api")

url = "https://columbiacountyso.policetocitizen.com/api/Inmates/526"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://columbiacountyso.policetocitizen.com/Inmates",
}

# Try payload 1: Empty search criteria
payloads = [
    # Empty parameters
    {
        "IntersectionSearch": True,
        "SearchText": "",
        "Parameters": []
    },
    # In custody filter
    {
        "IntersectionSearch": True,
        "SearchText": "",
        "Parameters": [
            {
                "Field": "In Custody",
                "Operation": "Equals",
                "Value": True
            }
        ]
    },
    # Simple empty dict
    {}
]

for idx, payload in enumerate(payloads):
    try:
        logger.info(f"Trying payload {idx}: {payload}")
        resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=15)
        logger.info(f"Response status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Response data keys: {data.keys()}")
            inmates = data.get("Inmates", [])
            logger.info(f"Found {len(inmates)} inmates in response!")
            if inmates:
                logger.info(f"First inmate snippet: {inmates[0]}")
                break
        else:
            logger.info(f"Response text: {resp.text[:500]}")
    except Exception as e:
        logger.error(f"Failed payload {idx}: {e}")
