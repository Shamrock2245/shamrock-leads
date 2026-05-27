import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
uri = os.getenv("MONGODB_URI")
db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
client = MongoClient(uri)
db = client[db_name]
col = db["scraper_status"]

counties = ["Charlotte", "Collier", "Manatee", "Hendry", "Sarasota"]
for doc in col.find({"county": {"$in": counties}}):
    print("--------------------------------------------------")
    print(f"County: {doc.get('county')}")
    print(f"Last Run: {doc.get('last_run')}")
    print(f"Status: {doc.get('status')}")
    print(f"Error: {doc.get('error')}")
    print(f"Records: {doc.get('records')}")
    print(f"Hot Leads: {doc.get('hot_leads')}")
    print(f"Warm Leads: {doc.get('warm_leads')}")
    print(f"Duration: {doc.get('duration_seconds')}s")
    print(f"Run Count: {doc.get('run_count')}")

client.close()
