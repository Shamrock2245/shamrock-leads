import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

mongo_uri = os.getenv("MONGODB_URI")
db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

print(f"Connecting to MongoDB at {mongo_uri[:30]}...")
client = MongoClient(mongo_uri)
db = client[db_name]

# Check arrests collection
arrests_col = db["arrests"]
count_bay = arrests_col.count_documents({"County": "Bay"})
print(f"Total Bay County records in database: {count_bay}")

# Print first few Bay County records if any
if count_bay > 0:
    print("First 3 records:")
    for doc in arrests_col.find({"County": "Bay"}).limit(3):
        print(f"- {doc.get('First_Name')} {doc.get('Last_Name')}, Booking: {doc.get('Booking_Number')}, Date: {doc.get('Booking_Date')}")
