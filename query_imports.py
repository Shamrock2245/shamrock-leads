import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def run():
    client = AsyncIOMotorClient("mongodb+srv://shamrock_leads:sRf5ra92sM2K4Ntn@shamrock.1mgkm.mongodb.net/ShamrockBailDB?retryWrites=true&w=majority&appName=Shamrock")
    db = client.ShamrockBailDB
    imports = await db.accounting_imports.find().sort("timestamp", -1).limit(1).to_list(1)
    if imports:
        print("IMPORTED:", imports[0]["imported"])
        print("SKIPPED:", imports[0]["skipped"])
        print("ERRORS:", imports[0]["errors"])
        print("DETAILS:", imports[0].get("error_details", []))
    else:
        print("No imports found.")

asyncio.run(run())
