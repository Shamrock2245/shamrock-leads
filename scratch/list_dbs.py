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
if not uri:
    print("MONGODB_URI not found!")
    sys.exit(1)

client = MongoClient(uri)
print("Databases:")
print(client.list_database_names())
for db_name in client.list_database_names():
    db = client[db_name]
    print(f"Collections in {db_name}:")
    print(db.list_collection_names())
