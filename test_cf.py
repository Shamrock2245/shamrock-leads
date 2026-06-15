from curl_cffi import requests
url = "https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663"
response = requests.get(url, impersonate="chrome110")
print(response.status_code)
print(response.text[:500])
