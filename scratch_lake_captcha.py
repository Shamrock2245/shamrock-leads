import requests
import time

API_KEY = "585d48daccc0f7ac029fa08c2226825c"
SITE_KEY = "6Ldas6IrAAAAAAuFfoBGxbpraKxvnnrHNaLLRjKx"
URL = "https://www.lcso.org/inmate-search/"

def solve_recaptcha():
    print("Submitting captcha task...")
    res = requests.get(f"http://api.solvecaptcha.com/in.php?key={API_KEY}&method=userrecaptcha&googlekey={SITE_KEY}&pageurl={URL}&json=1")
    data = res.json()
    if data.get("status") != 1:
        print("Error submitting:", data)
        return None
    
    task_id = data["request"]
    print(f"Task ID: {task_id}")
    
    for _ in range(20):
        time.sleep(5)
        res = requests.get(f"http://api.solvecaptcha.com/res.php?key={API_KEY}&action=get&id={task_id}&json=1")
        data = res.json()
        if data.get("status") == 1:
            print("Solved!")
            return data["request"]
        elif data.get("request") != "CAPCHA_NOT_READY":
            print("Error solving:", data)
            return None
        print("Waiting...")
    return None

token = solve_recaptcha()
if token:
    print("Token:", token[:20], "...")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
        "Referer": URL
    }
    payload = {
        "firstname": "",
        "lastname": "",
        "dob": "",
        "booking_number": "",
        "facility": "",
        "token": token
    }
    r = requests.post("https://www.lcso.org/inmate-search/api/inmates", json=payload, headers=headers)
    print("Status:", r.status_code)
    try:
        data = r.json()
        if isinstance(data, list):
            print("Got", len(data), "records")
            if data:
                print(data[0].get('firstname'), data[0].get('lastname'))
        else:
            print("Data:", str(data)[:200])
    except Exception as e:
        print("Error parsing json:", e)
        print(r.text[:200])
