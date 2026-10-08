#!/usr/bin/env python3
"""Lee FL direct READ smoke: plain HTTPS, no proxy, no stealth, no writes.

Uses the scraper's own fetch path (``LeeCountyScraper._http_fetch``: one
``requests`` session, honest User-Agent, normal DNS, ``trust_env=False``).
Prints one JSON line of counts and shapes only. No names, DOBs, addresses,
charges text or booking numbers are printed.

Run on the Leads Ops VPS (the prod scraper's egress):

    python scripts/lee_direct_smoke.py

Options: ``--pages N`` (default 2) caps listing pages per variant,
``--charges N`` (default 2) caps charges calls. Defaults cost at most
6 requests against Lee's per-IP quota.

Exit codes:
    0 direct read ok: 200 JSON rows, booking numbers all digits, no dupes
    2 blocked / challenged / rate-limited (403, 429, 503, HTML challenge,
      connect failure) or a Lee cooldown is already active
    3 empty or drift (200 but no rows, non-JSON body, odd booking shapes,
      duplicate booking numbers)
    1 anything else
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
import re
import socket
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROXY_ENV_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
    "SOCKS_PROXY", "SCRAPER_SOCKS_PROXY", "WARREN_PROXY_URL", "SCRAPFLY_API_KEY",
)
CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "just a moment", "captcha", "access denied")


def shape(value) -> str:
    """Booking-number shape only: digits -> N, letters -> A."""
    return re.sub(r"\d", "N", re.sub(r"[A-Za-z]", "A", str(value or "")))


def classify_page(status: int, ctype: str, body_head: str) -> str:
    """ok | blocked | drift for one HTTP response."""
    head = (body_head or "").lower()
    if status in (403, 429, 503) or any(m in head for m in CHALLENGE_MARKERS):
        return "blocked"
    if status != 200:
        return "blocked" if status >= 500 else "drift"
    if "json" not in (ctype or "").lower():
        return "drift"
    return "ok"


def evaluate(pages: list, numbers_by_variant: dict) -> tuple:
    """Return (result, exit_code) from per-page results and booking numbers.

    A booking may appear in both variants (in custody and booked in the
    window); a repeat inside one variant means paging drift.
    """
    states = [p["state"] for p in pages]
    if not states or "blocked" in states or "no_response" in states:
        return "blocked", 2
    if "drift" in states:
        return "drift", 3
    every = [n for nums in numbers_by_variant.values() for n in nums]
    if not every:
        return "empty", 3
    if not all(re.fullmatch(r"N+", shape(n)) for n in every):
        return "drift", 3
    if any(len(set(nums)) != len(nums) for nums in numbers_by_variant.values()):
        return "drift", 3
    return "ok", 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--charges", type=int, default=2)
    args = ap.parse_args(argv)

    import os
    from scrapers import lee_rate_limit
    from scrapers.counties import lee

    out = {
        "county": "Lee (FL)",
        "egress": "direct (requests, trust_env=False, no proxy, no stealth)",
        "user_agent": lee.LEE_USER_AGENT,
        "proxy_env_present_but_ignored": [n for n in PROXY_ENV_NAMES if os.getenv(n)],
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        out["www_dns"] = sorted({a[4][0] for a in socket.getaddrinfo("www.sheriffleefl.org", 443)})
    except OSError as exc:
        out["www_dns"] = f"resolve_failed: {type(exc).__name__}"

    if lee_rate_limit.is_cooled_down():
        out.update(result="blocked", reason="lee cooldown active",
                   cooldown_s_left=round(lee_rate_limit.seconds_remaining()))
        print(json.dumps(out))
        return 2

    scraper = lee.LeeCountyScraper()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lee.DAYS_BACK)
    variants = {
        "inCustody": {"inCustody": "true"},
        "date_range": {"startBooking": start.strftime("%Y-%m-%d"), "endBooking": end.strftime("%Y-%m-%d")},
    }
    pages, by_variant, in_custody = [], {}, Counter()
    date_shapes = Counter()
    try:
        for name, params in variants.items():
            by_variant[name] = []
            for page in range(max(1, args.pages)):
                q = {**params, "limit": lee.PAGE_SIZE, "offset": page * lee.PAGE_SIZE}
                resp = scraper._http_fetch(f"{lee.BASE_URL}{lee.BOOKINGS_API}", params=q)
                if resp is None:
                    pages.append({"variant": name, "page": page + 1, "state": "no_response"})
                    break
                ctype = resp.headers.get("content-type", "")
                state = classify_page(resp.status_code, ctype, resp.text[:2000])
                entry = {"variant": name, "page": page + 1, "http": resp.status_code,
                         "content_type": ctype.split(";")[0], "state": state}
                if resp.headers.get("cf-mitigated"):
                    entry["cf_mitigated"] = resp.headers.get("cf-mitigated")
                    entry["state"] = state = "blocked"
                if state == "ok":
                    try:
                        recs = lee.LeeCountyScraper._extract_records(resp.json())
                    except ValueError:
                        entry["state"] = state = "drift"
                        recs = []
                    entry["rows"] = len(recs)
                    for r in recs:
                        by_variant[name].append(str(r.get("bookingNumber") or ""))
                        in_custody[str(r.get("inCustody"))] += 1
                        date_shapes[shape(r.get("bookingDate"))] += 1
                pages.append(entry)
                if state != "ok" or entry.get("rows", 0) < lee.PAGE_SIZE:
                    break

        numbers = [n for nums in by_variant.values() for n in nums]
        unique = list(dict.fromkeys(n for n in numbers if n))
        charges = {"requested": 0, "http": Counter(), "with_rows": 0, "with_bond_amount": 0}
        for bn in unique[: max(0, args.charges)]:
            resp = scraper._http_fetch(f"{lee.BASE_URL}{lee.CHARGES_API.format(booking_id=bn)}")
            charges["requested"] += 1
            charges["http"][str(resp.status_code if resp is not None else "none")] += 1
            if resp is not None and resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                if isinstance(data, list) and data:
                    charges["with_rows"] += 1
                    if any(c.get("bondAmount") not in (None, "") for c in data):
                        charges["with_bond_amount"] += 1
        charges["http"] = dict(charges["http"])
    except Exception as exc:  # noqa: BLE001 - report and fail
        out.update(result="error", error=f"{type(exc).__name__}: {str(exc)[:300]}", pages=pages)
        print(json.dumps(out))
        return 1
    finally:
        scraper._cleanup()

    result, code = evaluate(pages, by_variant)
    out.update(
        result=result,
        pages=pages,
        rows_by_variant={k: len(v) for k, v in by_variant.items()},
        duplicate_within_variant={k: len(v) - len(set(v)) for k, v in by_variant.items()},
        rows_total=len(numbers),
        unique_booking_numbers=len(unique),
        blank_booking_numbers=sum(1 for n in numbers if not n),
        booking_number_shapes=dict(Counter(shape(n) for n in numbers)),
        booking_date_shapes=dict(date_shapes.most_common(3)),
        in_custody=dict(in_custody),
        charges=charges,
    )
    print(json.dumps(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
