import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://columbiacountyso.policetocitizen.com/Inmates"
API_URL = "https://columbiacountyso.policetocitizen.com/Inmates/GetInmates"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}

print("=== TESTING GET ===")
for url in [API_URL, BASE_URL + "/GetInmates", BASE_URL]:
    try:
        r = requests.get(url, headers=HEADERS, params={"page": 1, "pageSize": 10, "inCustody": True}, timeout=10, verify=False)
        print(f"GET {url}: status={r.status_code}, len={len(r.text)}, snippet={r.text[:300]}")
    except Exception as e:
        print(f"GET {url} failed: {e}")

print("=== TESTING POST ===")
for url in [API_URL, BASE_URL + "/GetInmates", BASE_URL]:
    try:
        # P2C often uses JSON POST body with paging parameters
        payload = {"page": 1, "pageSize": 10, "inCustody": True}
        r = requests.post(url, headers=HEADERS, json=payload, timeout=10, verify=False)
        print(f"POST {url} (JSON): status={r.status_code}, len={len(r.text)}, snippet={r.text[:300]}")
        
        r2 = requests.post(url, headers=HEADERS, data=payload, timeout=10, verify=False)
        print(f"POST {url} (form): status={r2.status_code}, len={len(r2.text)}, snippet={r2.text[:300]}")
    except Exception as e:
        print(f"POST {url} failed: {e}")
