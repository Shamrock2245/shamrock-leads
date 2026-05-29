import io
import pandas as pd
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
    df = pd.read_csv(
        io.StringIO(r.text),
        dtype=str,
        on_bad_lines="skip",
    )
    df.columns = [c.strip() for c in df.columns]
    
    # Search for ARREST_NUMBER '502677' or BIRH_DATE '10/3/1995'
    print("DataFrame rows where ARREST_NUMBER is 502677:")
    sub1 = df[df["ARREST_NUMBER"] == "502677"]
    print(sub1.to_dict(orient="records"))
    
    print("\nDataFrame rows where ARREST_NUMBER is 10/3/1995:")
    sub2 = df[df["ARREST_NUMBER"] == "10/3/1995"]
    print(sub2.to_dict(orient="records"))
    
    print("\nSearch by BIRTH_DATE 10/3/1995:")
    sub3 = df[df["BIRTH_DATE"] == "10/3/1995"]
    print(sub3.to_dict(orient="records"))

    print("\nPrint all rows where LAST_NAME is ALLAN:")
    sub4 = df[df["LAST_NAME"].str.strip() == "ALLAN"]
    print(sub4.to_dict(orient="records"))
else:
    print("Failed to download")
