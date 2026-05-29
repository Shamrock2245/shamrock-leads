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
coll = db["scraper_status"]

print("Scraper Status entries:")
for r in coll.find():
    print(f"County: {r.get('county') or r.get('County')}, Status: {r.get('status')}, LastRun: {r.get('last_run') or r.get('last_checked')}")
