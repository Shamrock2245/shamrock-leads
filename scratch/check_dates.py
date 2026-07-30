import asyncio
import os
import sys
from dotenv import load_dotenv

env_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env'
load_dotenv(dotenv_path=env_path)

sys.path.insert(0, '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads')
from dashboard.extensions import get_collection

async def main():
    col = get_collection("arrests")
    
    # Just find one record and see what dates look like
    doc = await col.find_one()
    if doc:
        print("Sample Record Dates:")
        for k in ['created_at', 'scraped_at', 'scrape_timestamp', 'Booking_Date']:
            if k in doc:
                val = doc[k]
                print(f"{k}: {val} (type: {type(val)})")
                
    # Find the earliest record
    earliest = await col.find().sort("scraped_at", 1).limit(1).to_list(1)
    if earliest:
        doc = earliest[0]
        print("\nEarliest record:")
        for k in ['created_at', 'scraped_at', 'scrape_timestamp', 'Booking_Date']:
            if k in doc:
                val = doc[k]
                print(f"{k}: {val} (type: {type(val)})")

if __name__ == "__main__":
    asyncio.run(main())
