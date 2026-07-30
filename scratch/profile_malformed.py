import asyncio
import os
import sys
import re
from dotenv import load_dotenv

env_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env'
load_dotenv(dotenv_path=env_path)

sys.path.insert(0, '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads')
from dashboard.extensions import get_collection
from dashboard.routers.data_retention import _get_protected_booking_numbers

async def main():
    col = get_collection("arrests")
    protected = await _get_protected_booking_numbers()
    
    total = await col.count_documents({})
    
    # Define malformed criteria
    # 1. Missing or empty booking number
    no_booking_num = await col.count_documents({"$or": [{"booking_number": {"$exists": False}}, {"booking_number": ""}, {"booking_number": None}]})
    
    # 2. Missing or empty county
    no_county = await col.count_documents({"$or": [{"county": {"$exists": False}}, {"county": ""}, {"county": None}]})
    
    # 3. Missing or empty name (full_name empty AND first_name/last_name empty)
    no_name = await col.count_documents({
        "$and": [
            {"$or": [{"full_name": {"$exists": False}}, {"full_name": ""}, {"full_name": None}]},
            {"$or": [{"last_name": {"$exists": False}}, {"last_name": ""}, {"last_name": None}]}
        ]
    })
    
    # 4. HTML tags in booking_number or name
    html_pattern = re.compile(r"<[^>]+>")
    html_in_booking = await col.count_documents({"booking_number": {"$regex": "<[^>]+>"}})
    html_in_name = await col.count_documents({"full_name": {"$regex": "<[^>]+>"}})
    
    print("--- MongoDB Arrests Profile ---")
    print(f"Total Records: {total}")
    print(f"Protected (Bonded/Do Not Touch): {len(protected)}")
    print("--- Malformed Counts ---")
    print(f"Missing Booking Number: {no_booking_num}")
    print(f"Missing County: {no_county}")
    print(f"Missing Name: {no_name}")
    print(f"HTML in Booking Number: {html_in_booking}")
    print(f"HTML in Name: {html_in_name}")
    
    # Build a combined query to count the union of all malformed criteria (excluding protected)
    combined_query = {
        "booking_number": {"$nin": list(protected)},
        "$or": [
            {"booking_number": {"$in": [None, ""]}},
            {"booking_number": {"$exists": False}},
            {"county": {"$in": [None, ""]}},
            {"county": {"$exists": False}},
            {"$and": [
                {"full_name": {"$in": [None, ""]}},
                {"last_name": {"$in": [None, ""]}}
            ]},
            {"booking_number": {"$regex": "<[^>]+>"}},
            {"full_name": {"$regex": "<[^>]+>"}}
        ]
    }
    
    total_malformed = await col.count_documents(combined_query)
    print(f"\nTotal unique unprotected MALFORMED records: {total_malformed}")

if __name__ == "__main__":
    asyncio.run(main())
