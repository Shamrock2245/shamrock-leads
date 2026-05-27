import os
import sys
import logging
from dotenv import load_dotenv

# Add project root to python path to import settings/scrapers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)

load_dotenv()

from scrapers.counties.indian_river import IndianRiverCountyScraper

def main():
    print("Initializing Indian River Scraper...")
    scraper = IndianRiverCountyScraper()
    
    booking_id = "1725627172"
    detail_url = f"https://www.ircsheriff.org/booking-details/{booking_id}"
    
    print(f"Calling _fetch_single_booking for {booking_id}...")
    record = scraper._fetch_single_booking(booking_id, detail_url)
    
    if record:
        print("\nSUCCESS! Parsed ArrestRecord successfully:")
        doc = record.to_mongo_doc()
        for k, v in doc.items():
            print(f"  {k}: {v} (type: {type(v).__name__})")
    else:
        print("\nFAILURE: _fetch_single_booking returned None")

if __name__ == "__main__":
    main()
