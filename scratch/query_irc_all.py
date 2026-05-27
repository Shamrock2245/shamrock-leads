import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

# Add project root to python path to import settings if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from config.settings import settings

def main():
    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    arrests = db["arrests"]
    
    # Query for any Indian River bookings
    query = {"county": "Indian River"}
    docs = list(arrests.find(query).limit(10))
    
    print(f"Found {len(docs)} documents for Indian River:")
    for doc in docs:
        print(f"Name: {doc.get('full_name')}, Booking #: {doc.get('booking_number')}, Bond Amt: {doc.get('bond_amount')}, Bond Raw: {doc.get('bond_amount_raw')}, Bond Type: {doc.get('bond_type')}, Status: {doc.get('status')}")
            
    client.close()

if __name__ == "__main__":
    main()
