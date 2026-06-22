from curl_cffi import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sheriffleefl.org/",
}
url = "https://www.sheriffleefl.org/public-api/bookings?inCustody=true&limit=2"

try:
    resp = requests.get(url, headers=headers, impersonate="chrome110")
    print("Status:", resp.status_code)
    print("Body:", resp.text[:200])
except Exception as e:
    print("Error:", e)
