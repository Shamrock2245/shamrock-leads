import io
from curl_cffi import requests as cf
from datetime import datetime, timedelta

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}

search_terms = ["1554655", "1554634", "1554639"]
print(f"Searching for terms: {search_terms}")

for days_ago in range(10):
    date = datetime.now() - timedelta(days=days_ago)
    date_str = date.strftime("%Y-%m-%d")
    url = f"https://apps.osceola.org/Apps/CorrectionsReports/Report/Download/{date_str}"
    
    try:
        r = cf.get(
            url,
            headers=headers,
            timeout=20,
            impersonate="chrome131",
            verify=False,
        )
        if r.status_code == 200:
            lines = r.text.splitlines()
            for idx, line in enumerate(lines):
                for term in search_terms:
                    if term in line:
                        print(f"Found {term} in {date_str} (line {idx}):")
                        print(line)
    except Exception as e:
        print(f"Error fetching {date_str}: {e}")
