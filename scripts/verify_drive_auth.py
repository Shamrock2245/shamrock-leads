#!/usr/bin/env python3
"""
Preflight check: Google Drive auth + Completed Bonds folder access.

Usage (from shamrock-leads root):
    python scripts/verify_drive_auth.py

Exit codes:
  0 — auth + folder OK (writable)
  1 — not configured / auth failed / folder not accessible
  2 — auth OK but folder missing or not writable
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

# Prefer a real local SA path when Docker-style env points at a missing file
_sa_default = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "creds", "service-account-key.json")
)
_env_sa = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
if (not _env_sa or not os.path.isfile(_env_sa)) and os.path.isfile(_sa_default):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _sa_default


def main() -> int:
    from dashboard.services.google_drive_service import GoogleDriveService

    print("🔍 Google Drive auth preflight")
    print("-" * 50)

    drive = GoogleDriveService()
    print(f"  configured:          {drive.is_configured}")
    print(f"  service_account:     {drive._has_service_account()}")
    print(f"  oauth_token:         {drive._has_oauth()}")
    print(f"  completed_bonds_id:  {drive.completed_bonds_folder_id() or '(unset)'}")
    print()

    result = drive.health_check()
    print(json.dumps(result, indent=2))
    print()

    if result.get("ok"):
        print(f"✅ Drive healthy (mode={result.get('auth_mode')})")
        if result.get("folder_name"):
            print(f"   Folder: {result['folder_name']}")
        return 0

    code = result.get("error_code") or "unknown"
    print(f"❌ Drive NOT healthy: {code}")
    if result.get("error"):
        print(f"   {result['error']}")
    if result.get("hint"):
        print(f"   Hint: {result['hint']}")

    if code in ("folder_not_found", "folder_not_writable"):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
