import logging
import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-columbia-api-get")

url = "https://columbiacountyso.policetocitizen.com/api/Inmates/526"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://columbiacountyso.policetocitizen.com/Inmates",
}

try:
    logger.info("Trying GET request...")
    resp = requests.get(url, headers=headers, verify=False, timeout=15)
    logger.info(f"GET Response status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        logger.info(f"GET Response data snippet: {str(data)[:500]}")
    else:
        logger.info(f"GET Response text: {resp.text[:500]}")
except Exception as e:
    logger.error(f"Failed GET: {e}")
