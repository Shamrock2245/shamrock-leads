import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

mongo_uri = os.getenv("MONGODB_URI")
db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

print(f"Connecting to MongoDB...")
client = MongoClient(mongo_uri)
db = client[db_name]

# Check arrests collection per county
arrests_col = db["arrests"]
pipeline = [
    {"$group": {"_id": "$county", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}}
]
results = list(arrests_col.aggregate(pipeline))

print("\n--- Record counts by County in database ---")
for r in results:
    print(f"{r['_id']}: {r['count']}")
