import os
from pymongo import MongoClient

mongo_uri = "mongodb+srv://shamrock_leads:sRf5ra92sM2K4Ntn@shamrock.1mgkm.mongodb.net/ShamrockBailDB?retryWrites=true&w=majority&appName=Shamrock"
client = MongoClient(mongo_uri)
db = client.ShamrockBailDB
collection = db.scraper_status

print(f"{'County'.ljust(15)} | {'Status'.ljust(8)} | {'Records'.ljust(8)} | {'Hot'.ljust(6)} | {'Warm'.ljust(6)} | {'Duration'.ljust(8)} | {'Last Run'.ljust(25)} | {'Error'}")
print("-" * 110)

statuses = sorted(list(collection.find({})), key=lambda x: x.get("county", ""))
for s in statuses:
    county = s.get("county", "Unknown")
    status = s.get("status", "N/A")
    records = s.get("records", 0)
    hot = s.get("hot", 0)
    warm = s.get("warm", 0)
    duration = s.get("duration", 0)
    last_run = s.get("last_run", s.get("timestamp", "N/A"))
    error = s.get("error")
    if error is None:
        error = ""
    error = str(error)[:50]
    
    print(f"{county.ljust(15)} | {status.ljust(8)} | {str(records).ljust(8)} | {str(hot).ljust(6)} | {str(warm).ljust(6)} | {f'{duration:.1f}s'.ljust(8)} | {str(last_run).ljust(25)} | {error}")

client.close()
