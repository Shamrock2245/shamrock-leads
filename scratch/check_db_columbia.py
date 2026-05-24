import os
import sys
import logging
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check-db-columbia")

mongo_uri = os.getenv("MONGODB_URI") or "mongodb+srv://..."  # We will read it from environment or dotenv
# Or we can read it from .env file

# Let's read .env file
env_vars = {}
try:
    with open(".env", "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().replace("'", "").replace('"', '')
except Exception as e:
    logger.error(f"Failed to read .env: {e}")

uri = env_vars.get("MONGODB_URI") or os.getenv("MONGODB_URI")
db_name = env_vars.get("MONGODB_DB_NAME") or os.getenv("MONGODB_DB_NAME") or "ShamrockBailDB"

if not uri:
    logger.error("MONGODB_URI not found!")
    sys.exit(1)

try:
    client = MongoClient(uri)
    db = client[db_name]
    coll = db["arrests"]
    
    # Query columbia records
    columbia_count = coll.count_documents({"County": "Columbia"})
    logger.info(f"Total Columbia records in DB: {columbia_count}")
    
    # Let's fetch latest 5 records
    records = list(coll.find({"County": "Columbia"}).sort("Booking_Date", -1).limit(5))
    logger.info(f"Latest 5 Columbia records:")
    for r in records:
        logger.info(f"Name: {r.get('Full_Name')}, Booking_Num: {r.get('Booking_Number')}, Date: {r.get('Booking_Date')}, SourceMode: {r.get('LastCheckedMode')}")
        
except Exception as e:
    logger.error(f"Failed to query DB: {e}")
