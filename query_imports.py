"""One-off: print latest accounting import stats. Uses MONGODB_URI from env."""
import asyncio
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
    imports = await db.accounting_imports.find().sort("timestamp", -1).limit(1).to_list(1)
    if imports:
        print("IMPORTED:", imports[0]["imported"])
        print("SKIPPED:", imports[0]["skipped"])
        print("ERRORS:", imports[0]["errors"])
        print("DETAILS:", imports[0].get("error_details", []))
    else:
        print("No imports found.")


if __name__ == "__main__":
    asyncio.run(run())
