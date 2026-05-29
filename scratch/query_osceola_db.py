import os
import sys
import logging
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("query-osceola-db")

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
    
    osceola_count = coll.count_documents({"County": "Osceola"})
    logger.info(f"Total Osceola records in DB: {osceola_count}")
    
    # Query specific booking number from screenshot: 1554655 or 1554634 or 1554639
    sample_booking_numbers = ["1554655", "1554634", "1554639"]
    logger.info("Querying sample booking numbers from the screenshot...")
    for bn in sample_booking_numbers:
        records = list(coll.find({"Booking_Number": bn}))
        if records:
            for r in records:
                logger.info(f"MATCH BY Booking_Number={bn}:")
                logger.info(f"  _id: {r.get('_id')}")
                logger.info(f"  Full_Name: {r.get('Full_Name')}")
                logger.info(f"  First_Name: {r.get('First_Name')}")
                logger.info(f"  Middle_Name: {r.get('Middle_Name')}")
                logger.info(f"  Last_Name: {r.get('Last_Name')}")
                logger.info(f"  Booking_Number: {r.get('Booking_Number')}")
                logger.info(f"  Booking_Date: {r.get('Booking_Date')}")
                logger.info(f"  Arrest_Date: {r.get('Arrest_Date')}")
                logger.info(f"  Charges: {r.get('Charges')}")
                logger.info(f"  DOB: {r.get('DOB')}")
        else:
            logger.info(f"No records found for Booking_Number={bn}")
            
    # Also query records by name matching ALLAN
    logger.info("Querying records where Full_Name contains 'ALLAN' for Osceola...")
    records = list(coll.find({"County": "Osceola", "Full_Name": {"$regex": "ALLAN", "$options": "i"}}).limit(5))
    for r in records:
        logger.info(f"Name Match:")
        logger.info(f"  _id: {r.get('_id')}")
        logger.info(f"  Full_Name: {r.get('Full_Name')}")
        logger.info(f"  Booking_Number: {r.get('Booking_Number')}")
        logger.info(f"  Booking_Date: {r.get('Booking_Date')}")
        logger.info(f"  Arrest_Date: {r.get('Arrest_Date')}")
        
    # Also get the latest 5 Osceola records
    logger.info("Latest 5 Osceola records:")
    records = list(coll.find({"County": "Osceola"}).sort("_id", -1).limit(5))
    for r in records:
        logger.info(f"  _id: {r.get('_id')}, Full_Name: {r.get('Full_Name')}, Booking_Number: {r.get('Booking_Number')}, Booking_Date: {r.get('Booking_Date')}")

except Exception as e:
    logger.error(f"Failed to query DB: {e}")
