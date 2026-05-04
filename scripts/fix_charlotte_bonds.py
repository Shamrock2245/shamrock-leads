"""
Fix Charlotte County Bond Amount Inflation — MongoDB Cleanup
=============================================================
Finds all Charlotte County records with bond_amount > $5M (parsing artifacts)
and resets them to 0. These are booking numbers / agency codes that were
incorrectly parsed as bond amounts by the old fallback regex.

Run on VPS:
    docker exec shamrock-leads python scripts/fix_charlotte_bonds.py

Or locally:
    python scripts/fix_charlotte_bonds.py --dry-run
"""

import os
import sys
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGODB_URI", "")
DB_NAME = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
COLLECTION = "arrests"
MAX_REASONABLE_BOND = 5_000_000  # $5M cap per record


def main():
    parser = argparse.ArgumentParser(description="Fix inflated Charlotte County bond amounts")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    if not MONGO_URI:
        print("ERROR: MONGODB_URI not set")
        sys.exit(1)

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    # Find all Charlotte records with absurdly high bond amounts
    query = {
        "county": {"$regex": "charlotte", "$options": "i"},
        "bond_amount": {"$gt": MAX_REASONABLE_BOND},
    }

    bad_records = list(coll.find(query, {
        "_id": 1, "full_name": 1, "booking_number": 1, "bond_amount": 1, "charges": 1
    }))

    print(f"\n{'='*60}")
    print(f"Charlotte County Bond Cleanup")
    print(f"{'='*60}")
    print(f"Found {len(bad_records)} records with bond > ${MAX_REASONABLE_BOND:,.0f}")
    print()

    if not bad_records:
        print("✅ No inflated bonds found — database is clean.")
        client.close()
        return

    for rec in bad_records:
        print(f"  🔴 {rec.get('full_name', 'UNKNOWN'):30s} "
              f"Book#{rec.get('booking_number', '???'):15s} "
              f"Bond: ${rec.get('bond_amount', 0):>15,.0f}")

    print()

    if args.dry_run:
        print("DRY RUN — no changes made. Remove --dry-run to apply fixes.")
    else:
        result = coll.update_many(query, {"$set": {"bond_amount": 0}})
        print(f"✅ Fixed {result.modified_count} records (bond_amount → $0)")

        # Also reset lead scores for these records since bond amount affects scoring
        ids = [r["_id"] for r in bad_records]
        coll.update_many(
            {"_id": {"$in": ids}},
            {"$set": {"lead_score": 0, "lead_status": "Cold"}}
        )
        print(f"✅ Reset lead scores for {len(ids)} affected records")

    client.close()
    print()


if __name__ == "__main__":
    main()
