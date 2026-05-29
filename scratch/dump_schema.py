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

print("One document from arrests:")
doc = coll.find_one()
print(doc)

print("\nAll distinct values of 'County' if any:")
print(list(coll.distinct("County"))[:20])

print("\nKeys in the first document:")
if doc:
    print(list(doc.keys()))
else:
    print("No document found in arrests")
