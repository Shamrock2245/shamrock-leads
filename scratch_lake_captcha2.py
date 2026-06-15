import requests
import json

URL = "https://www.lcso.org/inmate-search/api/inmates"
payload = {"token": "dummy_token"}
r = requests.post(URL, json=payload)
print(r.status_code)
print(r.text)
