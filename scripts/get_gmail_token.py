#!/usr/bin/env python3
"""
One-time OAuth2 token exchange for Gmail + Calendar API access.
Run this locally, authorize in browser, paste the refresh token into VPS .env.

Usage:
    pip install google-auth-oauthlib
    python scripts/get_gmail_token.py
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes needed for court email processing + calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

# From GCP Console: shamrock-bail-suite → Shamrock Bail Portal Web Client
CLIENT_CONFIG = {
    "web": {
        "client_id": "167447516147-is4h2qhcqa51qhlen97tpkcij33r5a1n.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8085/"],
    }
}


def main():
    print("=" * 60)
    print("  Shamrock Gmail + Calendar OAuth Token Exchange")
    print("=" * 60)
    print()
    print("A browser will open. Sign in with the Gmail account that")
    print("receives court scheduling emails, then approve access.")
    print()

    flow = InstalledAppFlow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://localhost:3000/callback",
    )

    # This opens a browser and runs a local server on port 3000
    # login_hint forces Google to pre-select the correct account
    creds = flow.run_local_server(
        port=8085,
        prompt="consent",
        access_type="offline",  # Forces refresh token
        login_hint="admin@shamrockbailbonds.biz",
    )

    print()
    print("✅ Authorization successful!")
    print()
    print("=" * 60)
    print("  REFRESH TOKEN (copy this to VPS .env)")
    print("=" * 60)
    print()
    print(f"GOOGLE_GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GOOGLE_CLIENT_ID={CLIENT_CONFIG['web']['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={CLIENT_CONFIG['web']['client_secret']}")
    print()
    print("Add these 3 lines to /opt/shamrock-leads/.env on the VPS.")
    print()

    # Also save full token info locally for reference
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    with open("scripts/gmail_token.json", "w") as f:
        json.dump(token_data, f, indent=2)
    print("Token saved to scripts/gmail_token.json (DO NOT commit this)")
    print()


if __name__ == "__main__":
    main()
