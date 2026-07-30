import asyncio
import sys
from dotenv import load_dotenv

env_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/.env'
load_dotenv(dotenv_path=env_path)

sys.path.insert(0, '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads')
from dashboard.extensions import get_collection

async def main():
    col = get_collection("arrests")
    unknown = await col.count_documents({"full_name": {"$regex": "(?i)^unknown"}})
    print(f"Name starts with Unknown: {unknown}")

if __name__ == "__main__":
    asyncio.run(main())
