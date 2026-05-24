import os
import sys
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGODB_URI", "")
DB_NAME = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

if not MONGO_URI:
    from dotenv import load_dotenv
    load_dotenv()
    MONGO_URI = os.getenv("MONGODB_URI", "")
    DB_NAME = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
coll_status = db["scraper_status"]

# List of counties shown in screenshot
counties = [
    "Brevard", "Columbia", "Duval", "Escambia", "Hardee", "Highlands", 
    "Hillsborough", "Jackson", "Leon", "Martin", "Nassau", "Okaloosa", 
    "Pasco", "Polk", "St. Johns", "Volusia"
]

print(f"{'County':20s} | {'Status':8s} | {'Records':7s} | {'Duration':8s} | {'Last Error / Status Detail'}")
print("-" * 100)

for county in sorted(counties):
    # Find the status doc for this county
    # County in db might be capitalized or not, let's do regex search
    doc = coll_status.find_one({"county": {"$regex": f"^{county}$", "$options": "i"}})
    if doc:
        status = doc.get("status", "unknown")
        records = str(doc.get("records", 0))
        duration = f"{doc.get('duration', 0.0):.1f}s"
        error = doc.get("error") or doc.get("message") or "OK"
        print(f"{county:20s} | {status:8s} | {records:7s} | {duration:8s} | {error}")
    else:
        print(f"{county:20s} | NOT FOUND in scraper_status collection")

client.close()
