import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

mongo_uri = os.getenv("MONGODB_URI")
db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

client = MongoClient(mongo_uri)
db = client[db_name]
arrests_col = db["arrests"]

doc = arrests_col.find_one()
print("Sample Document:")
import pprint
pprint.pprint(doc)
