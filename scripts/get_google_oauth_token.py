#!/usr/bin/env python3
"""
One-time OAuth2 token exchange for Gmail + Calendar + Drive.

Run locally, authorize as admin@shamrockbailbonds.biz, then paste tokens into
VPS / local .env.

Usage:
    pip install google-auth-oauthlib
    python scripts/get_google_oauth_token.py

Scopes (all required for production paperwork + court email):
  - gmail.readonly  — court email / discharge monitor
  - calendar        — court date sync
  - drive           — Completed Bonds PDF archive

After success, update BOTH (same refresh token is fine if all scopes granted):
  GOOGLE_GMAIL_REFRESH_TOKEN=...
  # optional dedicated alias:
  # GOOGLE_DRIVE_REFRESH_TOKEN=...   # same value works

Prefer service-account Drive when possible (no user re-auth):
  GOOGLE_APPLICATION_CREDENTIALS=creds/service-account-key.json
  # Share Completed Bonds folder with SA client_email as Editor
"""

from __future__ import annotations

import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Full production scopes — must re-consent when scopes change
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]

# From GCP Console: shamrock-bail-suite → OAuth client (Desktop / installed)
# Prefer env overrides so secrets are not the only source of truth.
_DEFAULT_CLIENT_ID = (
    "167447516147-is4h2qhcqa51qhlen97tpkcij33r5a1n.apps.googleusercontent.com"
)
_DEFAULT_CLIENT_SECRET = "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"


def _client_config() -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID", _DEFAULT_CLIENT_ID)
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", _DEFAULT_CLIENT_SECRET)
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8085/"],
        }
    }


def main() -> int:
    print("=" * 60)
    print("  Shamrock Gmail + Calendar + Drive OAuth Token Exchange")
    print("=" * 60)
    print()
    print("A browser will open. Sign in as admin@shamrockbailbonds.biz")
    print("and approve Gmail, Calendar, AND Drive access.")
    print()
    print("Scopes requested:")
    for s in SCOPES:
        print(f"  • {s}")
    print()

    config = _client_config()
    flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)

    creds = flow.run_local_server(
        port=8085,
        prompt="consent",  # force refresh token + full scope consent
        access_type="offline",
        login_hint="admin@shamrockbailbonds.biz",
    )

    granted = list(creds.scopes or [])
    print()
    print("✅ Authorization successful!")
    print()
    print("Granted scopes:")
    for s in granted:
        print(f"  • {s}")
    print()

    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("⚠️  WARNING: Missing expected scopes:")
        for s in missing:
            print(f"  • {s}")
        print("Drive filing will fail until Drive is granted.")
        print()

    print("=" * 60)
    print("  REFRESH TOKEN (copy into .env / VPS .env)")
    print("=" * 60)
    print()
    print(f"GOOGLE_GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GOOGLE_CLIENT_ID={config['installed']['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={config['installed']['client_secret']}")
    print()
    print("Also recommended for server-side Drive (no user token):")
    print("  GOOGLE_APPLICATION_CREDENTIALS=creds/service-account-key.json")
    print("  COMPLETED_BONDS_FOLDER_ID=1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs")
    print("  # Share that folder with bail-suite-sa@…iam.gserviceaccount.com as Editor")
    print()
    print("Verify:  python scripts/verify_drive_auth.py")
    print()

    out_path = os.path.join(os.path.dirname(__file__), "google_oauth_token.json")
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": granted,
    }
    with open(out_path, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token saved to {out_path} (DO NOT commit)")
    print()
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
