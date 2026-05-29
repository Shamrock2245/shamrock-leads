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

# Group by scraped_at (which is a datetime object in MongoDB)
pipeline = [
    {"$match": {"county": "Osceola"}},
    {"$group": {
        "_id": "$scraped_at",
        "count": {"$sum": 1},
        "has_shifted": {"$sum": {"$cond": [{"$regexMatch": {"input": "$booking_number", "regex": "/"}}, 1, 0]}}
    }},
    {"$sort": {"_id": -1}}
]

results = list(coll.aggregate(pipeline))
print("Osceola batches in database grouped by scraped_at:")
for r in results[:30]:
    print(f"Scraped At: {r['_id']}, Total Records: {r['count']}, Shifted Records: {r['has_shifted']}")
