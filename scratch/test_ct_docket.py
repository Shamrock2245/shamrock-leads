"""One-shot test for the CT statewide docket scraper."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper  # noqa: E402


def main():
    s = CTStatewideDockerScraper()
    recs = s.scrape()
    print(f"\nTotal records: {len(recs)}")
    if recs:
        r = recs[0]
        print(f"Sample: {r.Full_Name} | {r.Booking_Number} | {r.Court_Location} | {r.Court_Date}")
        # Validate required fields
        bad = [x for x in recs if not x.Booking_Number or not x.Full_Name or x.State != "CT"]
        print(f"Invalid records: {len(bad)}")
        assert not bad, "some records missing required fields"
        print("✅ All records valid")
    return 0 if recs else 1


if __name__ == "__main__":
    sys.exit(main())
