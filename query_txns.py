import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import json

async def run():
    client = AsyncIOMotorClient("mongodb+srv://shamrock_leads:sRf5ra92sM2K4Ntn@shamrock.1mgkm.mongodb.net/ShamrockBailDB?retryWrites=true&w=majority&appName=Shamrock")
    db = client.ShamrockBailDB
    txns = await db.transactions.find({"source": "swipesimple"}).limit(3).to_list(3)
    for t in txns:
        t.pop("_id", None)
        print(json.dumps(t, indent=2))

asyncio.run(run())
