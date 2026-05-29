import io
import pandas as pd
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
                if "1554655" in line or "1554634" in line or "ALLAN" in line or "ANN" in line:
                    print(f"\n--- Raw Line in {date_str} (line {idx}): ---")
                    print(line)
                    
                    # Parse this single line with pandas
                    # We will create a CSV with the header and this line
                    csv_data = lines[0] + "\n" + line
                    df = pd.read_csv(io.StringIO(csv_data), dtype=str)
                    df.columns = [c.strip() for c in df.columns]
                    print("Parsed by pandas:")
                    print(df.to_dict(orient="records")[0])
    except Exception as e:
        print(f"Error fetching {date_str}: {e}")
