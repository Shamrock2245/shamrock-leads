#!/usr/bin/env python3
"""Charlotte FL residential READ smoke (no writes, no names printed).

Run from the Leads Ops Mac on home ISP or an iPhone hotspot (VPN off):

    CHARLOTTE_EGRESS_MODE=direct python scripts/charlotte_residential_smoke.py

Prints one JSON line of aggregates. Exit codes:
    0 roster read and every drift guard passed
    2 egress block (Cloudflare challenge / host exit not residential)
    3 parse drift (columns, Released values, paging, totals, key collisions)
    1 anything else
The write path is the normal one-shot run with MONGODB_URI set:
    CHARLOTTE_EGRESS_MODE=direct python main.py Charlotte
See docs/recon/FL_CHARLOTTE_REVIZE_2026-10-07.md.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.scraper_resilience import EgressBlocked, ParseDriftError  # noqa: E402


def main() -> int:
    from scrapers.counties.charlotte import CharlotteCountyScraper, egress_mode

    out = {"county": "Charlotte (FL)", "egress_mode": os.getenv("CHARLOTTE_EGRESS_MODE", "direct")}
    scraper = CharlotteCountyScraper()
    try:
        out["egress_mode"] = egress_mode()
        records = scraper.scrape()
    except EgressBlocked as exc:
        out.update(result="egress_block", error=str(exc)[:400])
        print(json.dumps(out))
        return 2
    except ParseDriftError as exc:
        out.update(result="parse_drift", error=str(exc)[:400])
        print(json.dumps(out))
        return 3
    except Exception as exc:  # noqa: BLE001 - report and fail
        out.update(result="error", error=f"{type(exc).__name__}: {str(exc)[:400]}")
        print(json.dumps(out))
        return 1

    keys = [r.Booking_Number for r in records]
    out.update(
        result="ok",
        walk=getattr(scraper, "last_walk_meta", {}),
        bookings=len(records),
        unique_booking_numbers=len(set(keys)),
        duplicate_booking_numbers=len(keys) - len(set(keys)),
        status=dict(Counter(r.Status for r in records)),
        with_charges=sum(1 for r in records if r.Charges),
        multi_charge=sum(1 for r in records if " | " in r.Charges),
        with_arrest_date=sum(1 for r in records if r.Arrest_Date),
        with_mugshot=sum(1 for r in records if r.Mugshot_URL),
        bond_unknown=sum(1 for r in records if r.Bond_Amount == ""),
        bond_zero=sum(1 for r in records if r.Bond_Amount == "0"),
        booking_number_lengths=dict(Counter(len(k) for k in keys)),
    )
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
