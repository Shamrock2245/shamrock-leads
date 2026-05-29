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

# Find shifted Osceola records where booking_number contains '/'
query = {
    "county": "Osceola",
    "booking_number": {"$regex": "/"}
}

shifted_count = coll.count_documents(query)
print(f"Found {shifted_count} shifted Osceola records to delete.")

if shifted_count > 0:
    result = coll.delete_many(query)
    print(f"Successfully deleted {result.deleted_count} shifted Osceola records.")
else:
    print("No shifted Osceola records found to delete.")
