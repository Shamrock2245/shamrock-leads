import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.baysomobile.org/is/"
session = requests.Session()
session.headers.update(HEADERS)

print("GET request to base URL...")
resp = session.get(BASE_URL, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Cookies: {session.cookies.get_dict()}")
print(f"Response Headers: {dict(resp.headers)}")
