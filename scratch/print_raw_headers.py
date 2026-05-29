from curl_cffi import requests as cf

url = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Download/2026-05-27"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}

r = cf.get(
    url,
    headers=headers,
    timeout=20,
    impersonate="chrome131",
    verify=False,
)

if r.status_code == 200:
    lines = r.text.splitlines()
    print("Raw Header Line:")
    print(lines[0])
else:
    print(f"Failed: {r.status_code}")
