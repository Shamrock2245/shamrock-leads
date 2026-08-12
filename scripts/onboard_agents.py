import asyncio
import sys
import os
from dotenv import load_dotenv

sys.path.append("/Users/brendan/Desktop/shamrock-active-software/shamrock-leads")
load_dotenv()

from dashboard.extensions import get_collection

async def onboard():
    sub_agents = get_collection("sub_agents")
    
    agents = [
        {
            "agent_name": "Kayla Lukesic",
            "license_number": "G356764",
            "is_admin": True,
            "is_active": True,
            "email": "kaylalynn123992@gmail.com"
        },
        {
            "agent_name": "Jason Taylor",
            "license_number": "W214323",
            "is_admin": False,
            "is_active": True,
            "email": "crabman23999@me.com"
        }
    ]
    
    for agent in agents:
        result = await sub_agents.update_one(
            {"license_number": {"$regex": f"^{agent['license_number']}$", "$options": "i"}},
            {"$set": agent},
            upsert=True
        )
        print(f"Upserted agent: {agent['agent_name']} (modified_count={result.modified_count}, upserted_id={result.upserted_id})")

if __name__ == "__main__":
    asyncio.run(onboard())
