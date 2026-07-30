import asyncio
import os
import sys
from dotenv import load_dotenv
import pytz
from datetime import datetime

env_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env'
load_dotenv(dotenv_path=env_path)

sys.path.insert(0, '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads')
from dashboard.extensions import get_collection

async def main():
    col = get_collection("arrests")
    
    # Get the latest 5 records for Lee county sorted by scraped_at / scrape_timestamp
    print("Fetching latest Lee County records...")
    
    # Trying to sort by created_at which is a datetime field, or scrape_timestamp which is string.
    # _id implies insertion order which correlates with scrape time.
    cursor = col.find({"county": "Lee"}).sort([("created_at", -1), ("_id", -1)]).limit(10)
    records = await cursor.to_list(length=10)
    
    if not records:
        print("No records found for Lee county.")
        return
        
    for r in records:
        print(f"Booking: {r.get('booking_number')} | Created: {r.get('created_at')} | Scrape TS: {r.get('scrape_timestamp')}")

if __name__ == "__main__":
    asyncio.run(main())
