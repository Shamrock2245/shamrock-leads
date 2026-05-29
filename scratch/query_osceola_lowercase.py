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

# Distinct counties (lowercase 'county')
print("Distinct counties:")
print(coll.distinct("county"))

# Count Osceola
osceola_count = coll.count_documents({"county": "Osceola"})
print(f"\nTotal Osceola records: {osceola_count}")

# Find records with 'ALLAN'
print("\nSearching for 'ALLAN' in Osceola county...")
records = list(coll.find({"county": "Osceola", "full_name": {"$regex": "ALLAN", "$options": "i"}}).limit(5))
for r in records:
    print(r)

# Find records with booking number '1554655' or '1554634'
print("\nSearching for booking numbers '1554655', '1554634'...")
for bn in ["1554655", "1554634", "1554639"]:
    r = coll.find_one({"booking_number": bn})
    if r:
        print(f"Booking {bn}: {r}")
    else:
        print(f"Booking {bn} not found")
