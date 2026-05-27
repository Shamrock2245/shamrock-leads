import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

# Add project root to python path to import settings if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from config.settings import settings

def main():
    print(f"MongoDB Configured: {settings.mongo_configured()}")
    print(f"Database Name: {settings.MONGODB_DB_NAME}")
    
    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    arrests = db["arrests"]
    
    # Query for Joseph Clayton Cooler or anyone with last name Cooler
    query = {"full_name": {"$regex": "Cooler", "$options": "i"}}
    docs = list(arrests.find(query))
    
    print(f"Found {len(docs)} documents matching 'Cooler':")
    for doc in docs:
        print("\n" + "="*50)
        for k, v in doc.items():
            print(f"{k}: {v} (type: {type(v).__name__})")
            
    client.close()

if __name__ == "__main__":
    main()
