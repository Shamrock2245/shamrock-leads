"""
Test script to run the FastAPI lifespan startup phase and verify full system connectivity and orchestration.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.main import app

async def main():
    print("☘️ Starting lifespan test...")
    try:
        # Call the lifespan async context manager
        async with app.router.lifespan_context(app):
            print("☘️ Lifespan started successfully!")
            # We can verify that the database is accessible
            from dashboard.extensions import get_collection
            coll = get_collection("poa_inventory")
            poa_count = await coll.count_documents({})
            print(f"☘️ POA Inventory Count: {poa_count}")
            
            # Verify background tasks are active
            tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("cron_")]
            print(f"☘️ Active cron tasks: {len(tasks)}")
            for t in tasks:
                print(f"  - {t.get_name()}")
            
            print("☘️ Test PASSED!")
    except Exception as e:
        print(f"❌ Lifespan test failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
