import urllib.request, json, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('SIGNNOW_API_TOKEN')

TEMPLATE_MAP = {
    # OSI
    "osi_appearance": "b53c6ad815854b7999881180fb22467d1ce5c9c5",
    "osi_app_defendant": "a54101e40a014a6da115a3311af41b211bb8a4d1",
    "osi_app_indemnitor": "a8260ab5083842bead2eb132a233b664d9f7f457",
    "osi_contingent_note": "a794fb76b9ff44b6aa33ec9492bb21e7d83d1c07",
    "osi_disclosure": "3490799af3104fb6b464a75369614afbe3ca0ccb",
    "osi_indemnity": "14f05781a7984cd18d20ccf95dd2475ab5249a5b",
    "osi_privacy": "314f344fc9ce4cecaaf310082f1b8cce6e166fc4",
    # Palmetto
    "palmetto_appearance": "aee028bd3cf14a1c97a5a8cd52f416556c602078",
    "palmetto_app_defendant": "dd36ed655fba453a9cd562419ec61474175b9fbb",
    "palmetto_app_indemnitor": "dfdbf83c1ce145fba575d50cd0de2a3a00f2e2aa",
    "palmetto_contingent_note": "4e7498c471c24eaf9c1e74f26622ec96294d13f3",
    "palmetto_disclosure": "f5127521abdf4d98ab38d827f300c73df8996b6b",
    "palmetto_indemnity": "68822363e79048f0ae43fde8de83196ed83c40e5",
    "palmetto_privacy": "b52ad8469d12456e9c93f0b2f8e1a7428795c325",
}

for name, tid in TEMPLATE_MAP.items():
    req = urllib.request.Request(f"https://api.signnow.com/document/{tid}")
    req.add_header('Authorization', f'Bearer {token}')
    try:
        urllib.request.urlopen(req)
        print(f"[OK] {name}")
    except urllib.error.HTTPError as e:
        print(f"[FAIL] {name} - {e.code}")

