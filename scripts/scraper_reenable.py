#!/usr/bin/env python3
"""Manually re-enable an auto-disabled scraper.

BaseScraper auto-disables a scraper after 5 consecutive counted failures
(``scraper_status.auto_disabled = true``). It re-enables itself when a
scheduled canary run returns records, or an operator can clear it:

    python scripts/scraper_reenable.py "Lee (FL)"
    python scripts/scraper_reenable.py "Charleston (SC)" --by brendan
    python scripts/scraper_reenable.py --list        # show auto-disabled scopes

Equivalent dashboard actions: Health → ▶ Re-enable (POST /api/scraper/enable),
or Health → ⚡ Run (dashboard run-now runs the scraper as a canary).

Reads MONGODB_URI from the environment only; never prints it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _split_label(label: str) -> tuple[str, str]:
    match = re.match(r"^(.+?)\s*\(([A-Za-z]{2})\)$", (label or "").strip())
    if not match:
        raise SystemExit('county must be a registry label like "Lee (FL)"')
    return match.group(1).strip(), match.group(2).upper()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("county", nargs="?", help='Registry label, e.g. "Lee (FL)"')
    parser.add_argument("--by", default="cli", help="Operator name recorded in reenabled_by")
    parser.add_argument("--list", action="store_true", help="List auto-disabled scrapers and exit")
    args = parser.parse_args()

    from writers.mongo_writer import MongoWriter

    writer = MongoWriter()
    try:
        if args.list:
            rows = writer.scraper_status.find(
                {"auto_disabled": True},
                {"_id": 0, "county_label": 1, "consecutive_failures": 1,
                 "last_error_class": 1, "auto_disabled_at": 1},
            )
            found = False
            for row in rows:
                found = True
                print(
                    f"{row.get('county_label')}: {row.get('consecutive_failures')} failures "
                    f"[{row.get('last_error_class')}] since {row.get('auto_disabled_at')}"
                )
            if not found:
                print("No auto-disabled scrapers.")
            return 0
        if not args.county:
            parser.error("county is required unless --list is given")
        bare, state = _split_label(args.county)
        ok = writer.reenable_scraper(bare, state, by=f"manual:{args.by}")
        print(f"{bare} ({state}): {'re-enabled' if ok else 'no scraper_status document found'}")
        return 0 if ok else 1
    finally:
        writer.close()


if __name__ == "__main__":
    sys.exit(main())
