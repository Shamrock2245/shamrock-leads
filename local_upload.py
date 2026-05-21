import requests

url_login = "https://leads.shamrockbailbonds.biz/login"
url_upload = "https://leads.shamrockbailbonds.biz/api/accounting/import/swipesimple"
pin = "224545"

session = requests.Session()
r_login = session.post(url_login, json={"pin": pin})
print("Login status:", r_login.status_code)
print("Login cookies:", session.cookies.get_dict())

with open("/Users/brendan/Downloads/transactions (1).csv", "rb") as f:
    r_upload = session.post(url_upload, files={"file": f})
    
print("Upload status:", r_upload.status_code)
print("Upload response:", r_upload.text)
