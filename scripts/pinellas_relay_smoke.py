#!/usr/bin/env python3
"""Pinellas FL relay READ smoke (no writes, no names printed).

Run on the Leads Ops home relay (home ISP or iPhone hotspot, VPN off), after
``python -m playwright install chromium``:

    PINELLAS_EGRESS_MODE=direct python scripts/pinellas_relay_smoke.py

Modal debug (off by default; writes names-free per-modal JSONL only under
``logs/pinellas-modal-debug-<timestamp>.jsonl`` on the relay):

    PINELLAS_EGRESS_MODE=direct PINELLAS_MODAL_DEBUG=1 python scripts/pinellas_relay_smoke.py
    # same as: ... python scripts/pinellas_relay_smoke.py --debug

Uses the module's own path: stock Playwright Chromium, headless, honest bot
User-Agent, no proxy/stealth. Prints one JSON line of aggregates. Exit codes:
    0 roster read with source booking numbers
    2 egress block (this host's exit is not verified US residential)
    3 no keyed rows / the search form or charge modals did not render
    1 anything else
The write path is the normal one-shot run with MONGODB_URI set:
    PINELLAS_EGRESS_MODE=direct python main.py Pinellas
See docs/ops/PINELLAS_RELAY_RUN.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.scraper_resilience import EgressBlocked  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pinellas relay read smoke (no writes, no names)")
    ap.add_argument("--debug", action="store_true",
                    help="set PINELLAS_MODAL_DEBUG=1: per-modal JSONL diagnostics under logs/")
    args = ap.parse_args(argv)
    if args.debug:
        os.environ["PINELLAS_MODAL_DEBUG"] = "1"

    from scrapers.counties.pinellas import (
        MODAL_DEBUG_ENV, USER_AGENT, PinellasCountyScraper, egress_mode, modal_timeout_ms,
    )

    out = {"county": "Pinellas (FL)", "egress_mode": os.getenv("PINELLAS_EGRESS_MODE", "direct"),
           "user_agent": USER_AGENT, "modal_timeout_ms": modal_timeout_ms(),
           "modal_debug": os.getenv(MODAL_DEBUG_ENV, "") not in ("", "0")}
    scraper = PinellasCountyScraper()

    def _modal_fields() -> dict:
        return {
            "modal_attempts": getattr(scraper, "_modal_attempts", 0),
            "modal_failures_skipped": getattr(scraper, "_modal_failures", 0),
            "modal_failure_reasons": dict(getattr(scraper, "_modal_failure_reasons", {}) or {}),
            "debug_log": getattr(scraper, "debug_log_path", None),
        }
    try:
        out["egress_mode"] = egress_mode()
        records = scraper.scrape()
    except EgressBlocked as exc:
        out.update(result="egress_block", error=str(exc)[:400])
        print(json.dumps(out))
        return 2
    except Exception as exc:  # noqa: BLE001 - report and fail
        msg = str(exc)
        if "Subject Charge Report modal failed to render" in msg:
            out.update(result="modals_not_rendered", error=f"{type(exc).__name__}: {msg[:400]}",
                       **_modal_fields())
            print(json.dumps(out))
            return 3
        out.update(result="error", error=f"{type(exc).__name__}: {msg[:400]}", **_modal_fields())
        print(json.dumps(out))
        return 1

    keys = [r.Booking_Number for r in records]
    out.update(
        result="ok" if keys else "no_rows",
        bookings=len(records),
        unique_booking_numbers=len(set(keys)),
        duplicate_booking_numbers=len(keys) - len(set(keys)),
        **_modal_fields(),
        status=dict(Counter(r.Status for r in records)),
        with_charges=sum(1 for r in records if r.Charges),
        with_booking_date=sum(1 for r in records if r.Booking_Date),
        bond_positive=sum(1 for r in records if r.Bond_Amount not in ("", "0")),
        bond_zero_published=sum(1 for r in records if r.Bond_Amount == "0"),
        bond_unknown=sum(1 for r in records if r.Bond_Amount == ""),
        booking_number_lengths=dict(Counter(len(k) for k in keys)),
    )
    print(json.dumps(out))
    return 0 if keys else 3


if __name__ == "__main__":
    sys.exit(main())
