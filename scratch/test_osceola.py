import io
from curl_cffi import requests as cf
import pandas as pd
from datetime import datetime, timedelta

BASE_URL = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Download"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}

date = datetime.now() - timedelta(days=1)
date_str = date.strftime("%Y-%m-%d")
url = f"{BASE_URL}/{date_str}"

print(f"Fetching URL: {url}")
r = cf.get(
    url,
    headers=HEADERS,
    timeout=20,
    impersonate="chrome131",
    verify=False,
)
print(f"Status code: {r.status_code}")
print(f"Content length: {len(r.content)}")

if r.status_code == 200 and len(r.content) >= 100:
    df = pd.read_csv(
        io.StringIO(r.text),
        dtype=str,
        on_bad_lines="skip",
    )
    df.columns = [c.strip() for c in df.columns]
    print("CSV Columns:")
    print(df.columns.tolist())
    print("\nFirst row sample:")
    if not df.empty:
        print(df.iloc[0].to_dict())
    else:
        print("Empty DataFrame")
else:
    print("Failed to fetch or empty data")
