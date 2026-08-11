#!/usr/bin/env python3
"""
One-time OAuth2 token exchange for Gmail + Calendar + Drive API access.

Alias for get_google_oauth_token.py (kept so existing runbooks still work).

Usage:
    pip install google-auth-oauthlib
    python scripts/get_gmail_token.py
"""

from __future__ import annotations

import os
import runpy
import sys

if __name__ == "__main__":
    target = os.path.join(os.path.dirname(__file__), "get_google_oauth_token.py")
    # Execute sibling script as __main__ so its sys.exit(main()) runs
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
