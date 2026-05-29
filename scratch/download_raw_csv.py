import io
from curl_cffi import requests as cf
from datetime import datetime

url = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Download/2026-05-28"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}

print(f"Downloading raw CSV from: {url}")
r = cf.get(
    url,
    headers=headers,
    timeout=20,
    impersonate="chrome131",
    verify=False,
)

if r.status_code == 200:
    lines = r.text.splitlines()
    print(f"Total lines: {len(lines)}")
    # Find lines containing 502677
    found = False
    for i, line in enumerate(lines):
        if "502677" in line:
            print(f"Line {i}: {line}")
            found = True
    if not found:
        print("Not found in 2026-05-28")
else:
    print(f"Failed to download: {r.status_code}")
