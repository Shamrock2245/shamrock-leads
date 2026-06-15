import requests
from bs4 import BeautifulSoup

URL = "http://69.21.72.195/smartwebclient/jail.aspx"
headers = {"User-Agent": "Mozilla/5.0"}
resp = requests.get(URL, headers=headers, timeout=10)
print(resp.status_code)
soup = BeautifulSoup(resp.text, 'html.parser')
print("Inputs:", len(soup.find_all("input")))
