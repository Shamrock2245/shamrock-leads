import os
import sys
from pymongo import MongoClient

env_vars = {}
try:
    with open(".env", "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().replace("'", "").replace('"', '')
except Exception as e:
    pass

uri = env_vars.get("MONGODB_URI") or os.getenv("MONGODB_URI")
db_name = env_vars.get("MONGODB_DB_NAME") or os.getenv("MONGODB_DB_NAME") or "ShamrockBailDB"

client = MongoClient(uri)
db = client[db_name]
coll = db["arrests"]

# Find Osceola records where booking_number contains '/'
shifted_records = list(coll.find({
    "county": "Osceola",
    "booking_number": {"$regex": "/"}
}))

print(f"Total shifted Osceola records found: {len(shifted_records)}")
for r in shifted_records[:20]:
    print(f"ID: {r.get('_id')}")
    print(f"  full_name: {r.get('full_name')}")
    print(f"  booking_number (dob): {r.get('booking_number')}")
    print(f"  booking_date (arrest_num): {r.get('booking_date')}")
    print(f"  dob (city): {r.get('dob')}")
    print(f"  first_name (inmateid): {r.get('first_name')}")
    print(f"  last_name: {r.get('last_name')}")
    print(f"  middle_name: {r.get('middle_name')}")
    print(f"  scraped_at: {r.get('scraped_at')}")
