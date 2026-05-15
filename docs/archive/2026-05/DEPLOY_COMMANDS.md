# Fix: Gmail OAuth "unauthorized_client" Error

The refresh token was created with a different OAuth client than what's in .env.
We need to generate a NEW refresh token using your current client credentials.

## Option A: Quick Token Generator (run on your Mac, NOT the VPS)

### Step 1: Create the token script
Save this Python script locally and run it. It will open a browser for you to authorize.

```python
# save as: get_gmail_token.py
# run with: python3 get_gmail_token.py

from google_auth_oauthlib.flow import InstalledAppFlow

# Paste your GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET from .env
CLIENT_ID = "YOUR_CLIENT_ID_HERE"
CLIENT_SECRET = "YOUR_CLIENT_SECRET_HERE"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    scopes=SCOPES,
)

creds = flow.run_local_server(port=8080)

print("\n✅ SUCCESS! Copy this refresh token into your .env:\n")
print(f"GOOGLE_GMAIL_REFRESH_TOKEN={creds.refresh_token}")
print(f"\nToken: {creds.token[:30]}...")
```

### Step 2: Run it
python3 get_gmail_token.py

### Step 3: Sign in as admin@shamrockbailbonds.biz when the browser opens

### Step 4: Copy the new GOOGLE_GMAIL_REFRESH_TOKEN into:
- Local .env:  /Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env
- VPS .env:    /opt/shamrock-leads/.env  (via SSH)

### Step 5: Rebuild dashboard on VPS
docker compose build --no-cache dashboard && docker compose up -d dashboard
