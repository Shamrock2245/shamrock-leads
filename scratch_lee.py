import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.sheriffleefl.org/",
}
url = "https://www.sheriffleefl.org/public-api/bookings?inCustody=true&limit=2"
resp = requests.get(url, headers=HEADERS)
print(resp.status_code)
print(resp.text[:500])
