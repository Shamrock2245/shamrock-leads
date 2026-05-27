import os
import sys
import asyncio
from pathlib import Path

# Add project root to python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from dashboard.routers.stats import api_scraper_health

async def main():
    print("🧪 Verifying api_scraper_health() endpoint...")
    try:
        results = await api_scraper_health()
        if "error" in results:
            print(f"❌ Error returned from endpoint: {results['error']}")
            if "trace" in results:
                print(results["trace"])
            return

        print(f"✅ Endpoint returned {len(results)} county records")
        target_counties = ["Charlotte", "Collier", "Manatee", "Hendry", "Sarasota", "Glades", "Sumter"]
        for r in results:
            county = r.get("county")
            if county in target_counties:
                print("--------------------------------------------------")
                print(f"County: {county}")
                print(f"  Records (Total): {r.get('total_records')}")
                print(f"  Status Light: {r.get('status')} (Mapped from last run)")
                print(f"  Last Run: {r.get('last_run')}")
                print(f"  Hours Since Run: {r.get('hours_since_update')}h")
                print(f"  Error: {r.get('error')}")
                print(f"  Enabled: {r.get('enabled')}")
    except Exception as e:
        print(f"❌ Verification threw exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
