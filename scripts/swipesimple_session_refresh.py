#!/usr/bin/env python3
"""
CLI wrapper: SwipeSimple session refresh / bootstrap (NOT invoice create).

Delegates to dashboard.services.swipesimple_playwright_bootstrap.
Primary invoice create path remains HTTP replay in swipesimple_invoice_service.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/swipesimple_session_refresh.py` from repo root
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.services.swipesimple_playwright_bootstrap import main

if __name__ == "__main__":
    main()
