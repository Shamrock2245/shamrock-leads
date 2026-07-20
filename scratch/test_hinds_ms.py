"""One-shot test for the Hinds County MS scraper (limits pages/details for speed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scrapers.counties_ms.hinds as hinds_mod  # noqa: E402

# Speed limits for smoke test
hinds_mod.MAX_PAGES = 3
hinds_mod.MAX_DETAILS = 3

from scrapers.counties_ms.hinds import HindsScraper  # noqa: E402


def main():
    s = HindsScraper()
    recs = s.scrape()
    print(f"\nTotal records: {len(recs)}")
    if recs:
        enriched = [r for r in recs if r.Charges]
        r = enriched[0] if enriched else recs[0]
        print(f"Sample: {r.Full_Name} | pin={r.Booking_Number} | arrest={r.Arrest_Date}")
        print(f"Charges: {r.Charges[:150]}")
        print(f"Agency: {r.Agency}")
        bad = [x for x in recs if not x.Booking_Number or not x.Full_Name or x.State != "MS"]
        print(f"Invalid records: {len(bad)}")
        assert not bad
        print("✅ All records valid")
    return 0 if recs else 1


if __name__ == "__main__":
    sys.exit(main())
