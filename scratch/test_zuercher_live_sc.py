"""
Live smoke test for the rewritten Zuercher parsing logic against the 8 SC
Zuercher portals. Tests hold_reasons parsing + API pagination.

Imports the real parser from scrapers.zuercher_base (with stubbed heavy deps).

Run: python3 scratch/test_zuercher_live_sc.py
"""
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import only the module-level regexes and static parser without triggering
# BaseScraper's heavy imports if possible; fall back to direct import.
try:
    from scrapers.zuercher_base import ZuercherBaseScraper
    parse_hold_reasons = ZuercherBaseScraper._parse_hold_reasons
except Exception as exc:  # heavy deps missing in sandbox — inline fallback
    print(f"(note: importing module fallback due to: {exc})")
    src = (Path(__file__).resolve().parent.parent / "scrapers" / "zuercher_base.py").read_text()
    ns = {"re": re, "List": list}
    # Execute just the regex definitions
    for block in src.split("\n\n"):
        if block.strip().startswith(("_HOLD_SPLIT_RE", "_CHARGE_LABEL_RE", "_BOND_RE", "_ARREST_DATE_RE")):
            exec(block, ns)
    raise SystemExit("Could not import parser — run inside repo venv")

PORTAL_API_PATH = "/api/portal/inmates/load"

SC_ZUERCHER = {
    "Anderson": "anderson-so-sc.zuercherportal.com",
    "Cherokee": "cherokee-so-sc.zuercherportal.com",
    "Colleton": "colleton-so-sc.zuercherportal.com",
    "Kershaw": "kershaw-so-sc.zuercherportal.com",
    "Laurens": "laurens-911-sc.zuercherportal.com",
    "Oconee": "oconee-so-sc.zuercherportal.com",
    "Pickens": "pickens-so-sc.zuercherportal.com",
    "Union": "union-so-sc.zuercherportal.com",
}


def unit_tests():
    """Simulated payload unit tests (TDD requirement)."""
    # Format 1: Warrant Charge with statute parenthetical, $0 bond
    sample = (
        "Warrant Charge: Family Court Bench Warrant warrant 2024DR0401828P-002 "
        "issued by County, SC (63-05-0020 (A) - 762 - Children / Support, "
        "obligation to support spouse and children); Arrest Date 06/21/2026; "
        "Bond - $0.00;"
    )
    charges, cents = parse_hold_reasons(sample)
    assert charges, f"expected charges, got {charges}"
    assert cents == 0, f"expected 0 cents, got {cents}"

    # Format 2: multi-segment with <br /> and 'Bond - Cash/Surety, $X'
    sample2 = (
        "Warrant: Felony Arrest warrant 2025A1510100487 issued by Colleton, SC; "
        "Arrest Date 12/30/2025; Bond - Cash/Surety, $15000.00; Set By X;<br />"
        "Warrant: Felony Arrest warrant 2025A1510100486 issued by Colleton, SC; "
        "Arrest Date 12/30/2025; Bond - Cash/Surety, $5000.00; Set By X;"
    )
    charges2, cents2 = parse_hold_reasons(sample2)
    assert len(charges2) >= 1, f"expected charges, got {charges2}"
    assert cents2 == 2000000, f"expected 2000000 cents ($20k), got {cents2}"

    # Format 3: FTP hold with no dollar bond
    sample3 = "FTP Support: Unspecified warrant ; Arrest Date 07/08/2026;"
    charges3, cents3 = parse_hold_reasons(sample3)
    assert charges3, f"expected FTP charge captured, got {charges3}"
    assert cents3 == 0

    # Format 4: empty
    charges4, cents4 = parse_hold_reasons("")
    assert charges4 == [] and cents4 == 0

    print("✅ Unit tests passed (hold_reasons parser, integer-cent bond math)")


def live_probe():
    print("\n── Live probe: 8 SC Zuercher portals ──")
    ok = 0
    for county, domain in SC_ZUERCHER.items():
        base = f"https://{domain}"
        payload = {
            "name": "", "race": "all", "sex": "all", "cell_block": "all",
            "held_for_agency": "any", "in_custody_date": "", "include_all": True,
            "paging": {"count": 5, "start": 0},
            "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
        }
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Referer": base + "/",
            })
            s.get(base, timeout=12)
            r = s.post(base + PORTAL_API_PATH, json=payload, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  {county}: HTTP {r.status_code}")
                continue
            data = r.json()
            total = data.get("total_record_count")
            recs = data.get("records", [])
            n_charges = 0
            n_bond = 0
            for rec in recs:
                ch, cents = parse_hold_reasons(rec.get("hold_reasons", ""))
                if ch:
                    n_charges += 1
                if cents > 0:
                    n_bond += 1
            print(f"  ✅ {county}: total={total}, sampled={len(recs)}, "
                  f"parsed_charges={n_charges}, parsed_bonds={n_bond}")
            ok += 1
        except Exception as e:
            print(f"  ❌ {county}: {type(e).__name__}: {e}")
        time.sleep(0.5)
    print(f"\n{ok}/{len(SC_ZUERCHER)} portals responding to JSON API")
    return ok


if __name__ == "__main__":
    unit_tests()
    ok = live_probe()
    sys.exit(0 if ok >= 6 else 1)
