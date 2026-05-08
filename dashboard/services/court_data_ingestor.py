"""
Court Data Ingestor — ShamrockLeads Intelligence Suite
======================================================

Orchestrates court opinion ingestion from CourtListener API
into the `court_outcomes` MongoDB collection. Handles:
  • Bulk ingestion of recent opinions (SE US, 12 states)
  • Deduplication via source_id
  • Defendant fuzzy-matching against arrests collection
  • Disposition statistics aggregation
"""

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("shamrock.court_ingestor")


async def run_ingestion(db, days_back: int = 30, states: list = None) -> dict:
    """Execute a full court opinion ingestion cycle.

    Args:
        db: Motor database instance
        days_back: How many days back to pull opinions
        states: Optional list of state codes (defaults to all SE US)

    Returns:
        dict with ingestion stats
    """
    from dashboard.services.courtlistener_client import CourtListenerClient
    import os

    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    client = CourtListenerClient(api_token=token if token else None)

    try:
        # Pull opinions from CourtListener
        opinions = await client.ingest_recent_opinions(
            days_back=days_back,
            states=states,
            page_size=20,
        )

        if not opinions:
            return {
                "success": True,
                "ingested": 0,
                "duplicates": 0,
                "message": "No opinions found for the given period",
            }

        # Deduplicate against existing records
        collection = db["court_outcomes"]
        inserted = 0
        duplicates = 0

        for opinion in opinions:
            source_id = opinion.get("source_id", "")
            if not source_id:
                continue

            # Check for existing record
            existing = await collection.find_one(
                {"source": "courtlistener", "source_id": source_id}
            )
            if existing:
                duplicates += 1
                continue

            # Attempt defendant matching
            match_result = await _fuzzy_match_defendant(
                db, opinion.get("case_name", "")
            )
            if match_result:
                opinion["matched_defendant_id"] = match_result.get("defendant_id")
                opinion["matched_defendant_name"] = match_result.get("name")
                opinion["match_confidence"] = match_result.get("confidence", 0)

            await collection.insert_one(opinion)
            inserted += 1

        log.info(
            "Ingestion complete: %d inserted, %d duplicates",
            inserted, duplicates,
        )

        return {
            "success": True,
            "ingested": inserted,
            "duplicates": duplicates,
            "total_fetched": len(opinions),
            "states_queried": states or "all_se_us",
            "days_back": days_back,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }

    except Exception as e:
        log.exception("Ingestion error: %s", e)
        return {"success": False, "error": str(e)}
    finally:
        await client.close()


async def get_ingestion_stats(db) -> dict:
    """Get current court_outcomes collection statistics."""
    collection = db["court_outcomes"]

    total = await collection.count_documents({})
    if total == 0:
        return {
            "success": True,
            "total_outcomes": 0,
            "by_state": [],
            "by_disposition": [],
            "by_court_type": [],
            "last_ingestion": None,
            "matched_defendants": 0,
        }

    # Aggregate by state
    state_pipeline = [
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    state_cursor = collection.aggregate(state_pipeline)
    by_state = await state_cursor.to_list(length=50)

    # Aggregate by disposition
    disp_pipeline = [
        {"$group": {"_id": "$disposition", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    disp_cursor = collection.aggregate(disp_pipeline)
    by_disposition = await disp_cursor.to_list(length=20)

    # Aggregate by court type
    type_pipeline = [
        {"$group": {"_id": "$court_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    type_cursor = collection.aggregate(type_pipeline)
    by_court_type = await type_cursor.to_list(length=10)

    # Count matched defendants
    matched = await collection.count_documents(
        {"matched_defendant_id": {"$exists": True, "$ne": None}}
    )

    # Last ingestion timestamp
    last_doc = await collection.find_one(
        {}, {"ingested_at": 1}, sort=[("ingested_at", -1)]
    )
    last_ingestion = last_doc.get("ingested_at") if last_doc else None

    return {
        "success": True,
        "total_outcomes": total,
        "by_state": [{"state": s["_id"], "count": s["count"]} for s in by_state],
        "by_disposition": [{"disposition": d["_id"], "count": d["count"]} for d in by_disposition],
        "by_court_type": [{"type": t["_id"], "count": t["count"]} for t in by_court_type],
        "matched_defendants": matched,
        "last_ingestion": last_ingestion,
    }


async def get_disposition_rates(db, state: str = None) -> dict:
    """Calculate empirical disposition rates for the predictor.

    Returns rates like: conviction 45%, dismissed 20%, plea 15%, etc.
    Optionally filtered by state.
    """
    collection = db["court_outcomes"]
    match_filter = {}
    if state:
        match_filter["state"] = state.upper()

    # Exclude 'unknown' dispositions from rate calculation
    match_filter["disposition"] = {"$ne": "unknown"}

    pipeline = [
        {"$match": match_filter},
        {"$group": {"_id": "$disposition", "count": {"$sum": 1}}},
    ]
    cursor = collection.aggregate(pipeline)
    results = await cursor.to_list(length=20)

    total = sum(r["count"] for r in results)
    if total == 0:
        return {"success": True, "rates": {}, "sample_size": 0, "state": state}

    rates = {}
    for r in results:
        rates[r["_id"]] = round(r["count"] / total, 4)

    return {
        "success": True,
        "rates": rates,
        "sample_size": total,
        "state": state,
    }


async def _fuzzy_match_defendant(db, case_name: str) -> Optional[dict]:
    """Attempt to match a case name to an existing defendant.

    Uses simple "LAST, FIRST" extraction from case names like
    "State v. Smith" or "Smith v. State of Florida".

    Returns match dict or None if no confident match.
    """
    if not case_name:
        return None

    # Extract defendant name from common case name patterns
    name_parts = None
    case_lower = case_name.lower()

    # Pattern: "State v. LastName" or "State of Florida v. LastName"
    for prefix in ["state v. ", "state of florida v. ", "people v. ",
                   "commonwealth v. ", "united states v. ", "u.s. v. "]:
        if prefix in case_lower:
            idx = case_lower.index(prefix) + len(prefix)
            name_parts = case_name[idx:].strip().split(",")[0].strip()
            break

    if not name_parts or len(name_parts) < 3:
        return None

    # Search arrests collection for matching last name
    try:
        search_name = name_parts.upper()
        cursor = db.arrests.find(
            {"$or": [
                {"full_name": {"$regex": search_name, "$options": "i"}},
                {"Defendant_Name": {"$regex": search_name, "$options": "i"}},
            ]},
            {"_id": 1, "full_name": 1, "Defendant_Name": 1, "defendant_id": 1},
        ).limit(5)
        matches = await cursor.to_list(length=5)

        if matches:
            best = matches[0]
            return {
                "defendant_id": str(best.get("defendant_id") or best["_id"]),
                "name": best.get("full_name") or best.get("Defendant_Name", ""),
                "confidence": 0.6 if len(matches) == 1 else 0.4,
            }
    except Exception as e:
        log.debug("Defendant match error: %s", str(e)[:100])

    return None
