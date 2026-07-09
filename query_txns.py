"""One-off: print sample SwipeSimple transactions. Uses MONGODB_URI from env."""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def run():
    uri = os.getenv("MONGODB_URI", "")
    if not uri:
        print("MONGODB_URI not set — copy .env.example and fill credentials", file=sys.stderr)
        sys.exit(1)

    client = AsyncIOMotorClient(uri)
    db = client[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]
    txns = await db.transactions.find({"source": "swipesimple"}).limit(3).to_list(3)
    for t in txns:
        t.pop("_id", None)
        print(json.dumps(t, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(run())
