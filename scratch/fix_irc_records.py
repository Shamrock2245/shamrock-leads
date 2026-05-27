import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from config.settings import settings
from scrapers.counties.indian_river import IndianRiverCountyScraper
from scoring.lead_scorer import LeadScorer

def main():
    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    arrests = db["arrests"]
    
    scraper = IndianRiverCountyScraper()
    scorer = LeadScorer()
    
    # 1. Specifically target Cooler, Joseph Clayton
    query = {"full_name": {"$regex": "Cooler", "$options": "i"}, "county": "Indian River"}
    doc = arrests.find_one(query)
    
    if doc:
        print(f"Found Joseph Clayton Cooler record in MongoDB:")
        print(f"  Current Bond Amount: {doc.get('bond_amount')}")
        print(f"  Current Bond Type: {doc.get('bond_type')}")
        
        booking_id = doc.get("booking_number")
        detail_url = doc.get("detail_url")
        
        print(f"Refetching live record for {booking_id}...")
        record = scraper._fetch_single_booking(booking_id, detail_url)
        if record:
            # Re-score
            scorer.score_and_update(record)
            mongo_doc = record.to_mongo_doc()
            
            # Correct the document in MongoDB
            result = arrests.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "bond_amount": mongo_doc["bond_amount"],
                    "bond_amount_raw": mongo_doc["bond_amount_raw"],
                    "bond_type": mongo_doc["bond_type"],
                    "lead_score": mongo_doc["lead_score"],
                    "lead_status": mongo_doc["lead_status"],
                    "updated_at": mongo_doc["updated_at"]
                }}
            )
            print(f"Successfully corrected Cooler's record in MongoDB (Modified: {result.modified_count})")
            
            # Print corrected document fields
            updated_doc = arrests.find_one({"_id": doc["_id"]})
            print("Corrected fields:")
            print(f"  Bond Amount: {updated_doc.get('bond_amount')}")
            print(f"  Bond Type: {updated_doc.get('bond_type')}")
            print(f"  Lead Score: {updated_doc.get('lead_score')}")
            print(f"  Lead Status: {updated_doc.get('lead_status')}")
        else:
            print("Failed to fetch live record for Cooler")
            
    # 2. Check for other Indian River records with huge bond amounts that might be commissary numbers
    print("\nChecking for other potential commissary-bond glitches in Indian River...")
    query_huge = {
        "county": "Indian River",
        "bond_amount": {"$gte": 1_000_000}
    }
    huge_docs = list(arrests.find(query_huge))
    print(f"Found {len(huge_docs)} huge bond records in Indian River")
    for huge_doc in huge_docs:
        print(f"Huge bond found: {huge_doc.get('full_name')} - Bond: {huge_doc.get('bond_amount')}")
        booking_id = huge_doc.get("booking_number")
        detail_url = huge_doc.get("detail_url")
        if detail_url:
            print(f"Refetching live record for {huge_doc.get('full_name')}...")
            record = scraper._fetch_single_booking(booking_id, detail_url)
            if record:
                scorer.score_and_update(record)
                mongo_doc = record.to_mongo_doc()
                result = arrests.update_one(
                    {"_id": huge_doc["_id"]},
                    {"$set": {
                        "bond_amount": mongo_doc["bond_amount"],
                        "bond_amount_raw": mongo_doc["bond_amount_raw"],
                        "bond_type": mongo_doc["bond_type"],
                        "lead_score": mongo_doc["lead_score"],
                        "lead_status": mongo_doc["lead_status"],
                        "updated_at": mongo_doc["updated_at"]
                    }}
                )
                print(f"Corrected {huge_doc.get('full_name')} to Bond: {mongo_doc['bond_amount']} (Modified: {result.modified_count})")
            else:
                print(f"Failed to refetch {huge_doc.get('full_name')}")
                
    client.close()

if __name__ == "__main__":
    main()
