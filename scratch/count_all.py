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

print("Document counts per collection:")
for col_name in db.list_collection_names():
    count = db[col_name].count_documents({})
    if count > 0:
        print(f"  {col_name}: {count}")

# Check if 'ALLAN' exists anywhere in any collection
print("\nSearching for 'ALLAN' in collections...")
for col_name in ['arrests', 'defendants', 'leads', 'prospective_bonds']:
    col = db[col_name]
    found = list(col.find({"$or": [
        {"Full_Name": {"$regex": "ALLAN", "$options": "i"}},
        {"First_Name": {"$regex": "ALLAN", "$options": "i"}},
        {"Last_Name": {"$regex": "ALLAN", "$options": "i"}},
        {"Name": {"$regex": "ALLAN", "$options": "i"}}
    ]}).limit(2))
    if found:
        print(f"Found in {col_name}:")
        for f in found:
            print(f"  {f}")
