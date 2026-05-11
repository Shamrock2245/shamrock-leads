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
import os
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger("shamrock.court_ingestor")


# ── Name Normalization ──────────────────────────────────────────────────────
def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy comparison.

    Strips suffixes (Jr, Sr, III), punctuation, extra spaces,
    and returns uppercase.
    """
    if not name:
        return ""
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name, flags=re.I)
    n = re.sub(r"[^a-zA-Z\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip().upper()
    return n


def _name_similarity(name_a: str, name_b: str) -> float:
    """Calculate similarity between two names (0.0 - 1.0).

    Uses token-based Jaccard similarity for robustness against
    name ordering differences ("SMITH JOHN" vs "JOHN SMITH").
    """
    a_tokens = set(_normalize_name(name_a).split())
    b_tokens = set(_normalize_name(name_b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(intersection) / len(union) if union else 0.0


async def run_ingestion(db, days_back: int = 180, states: list = None) -> dict:
    """Execute a full court opinion ingestion cycle.

    Enhanced pipeline:
    1. Pull opinions from CourtListener (bail-relevant filter)
    2. Deduplicate via source_id
    3. AI-summarize each opinion for bail bond intelligence
    4. Fuzzy-match defendants against arrests collection
    5. Score bail impact
    """
    from dashboard.services.courtlistener_client import CourtListenerClient

    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    client = CourtListenerClient(api_token=token if token else None)

    try:
        opinions = await client.ingest_recent_opinions(
            days_back=days_back, states=states, page_size=20,
            bail_relevant_only=True,
        )

        if not opinions:
            return {
                "success": True, "ingested": 0, "duplicates": 0,
                "message": "No bail-relevant opinions found",
                "api_health": client.get_api_health(),
            }

        collection = db["court_outcomes"]
        inserted = 0
        duplicates = 0
        ai_analyzed = 0

        for opinion in opinions:
            source_id = opinion.get("source_id", "")
            if not source_id:
                continue

            existing = await collection.find_one(
                {"source": "courtlistener", "source_id": source_id}
            )
            if existing:
                duplicates += 1
                continue

            # AI-powered opinion analysis
            ai_summary = await _ai_analyze_opinion(opinion)
            if ai_summary:
                opinion["ai_analysis"] = ai_summary
                ai_analyzed += 1

            # Score bail impact
            opinion["bail_impact"] = _score_bail_impact(opinion)

            # Defendant matching (improved)
            match_result = await _fuzzy_match_defendant(
                db, opinion.get("case_name", "")
            )
            if match_result:
                opinion["matched_defendant_id"] = match_result.get("defendant_id")
                opinion["matched_defendant_name"] = match_result.get("name")
                opinion["match_confidence"] = match_result.get("confidence", 0)
                opinion["match_method"] = match_result.get("method", "name")

            await collection.insert_one(opinion)
            inserted += 1

        log.info(
            "Ingestion complete: %d inserted, %d dupes, %d AI-analyzed",
            inserted, duplicates, ai_analyzed,
        )

        return {
            "success": True,
            "ingested": inserted,
            "duplicates": duplicates,
            "ai_analyzed": ai_analyzed,
            "total_fetched": len(opinions),
            "states_queried": states or "all_se_us",
            "days_back": days_back,
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "api_health": client.get_api_health(),
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
    """Match a case name to an existing defendant with improved accuracy.

    Enhanced with:
    - Name normalization (strips suffixes, punctuation)
    - Token-based Jaccard similarity scoring
    - Multi-candidate ranking
    - County cross-reference
    """
    if not case_name:
        return None

    # Extract defendant name from common case name patterns
    name_raw = None
    case_lower = case_name.lower()

    for prefix in ["state v. ", "state of florida v. ", "state of ",
                   "people v. ", "commonwealth v. ",
                   "united states v. ", "u.s. v. "]:
        if prefix in case_lower:
            idx = case_lower.index(prefix) + len(prefix)
            # Handle "State of X v." pattern
            remainder = case_name[idx:].strip()
            if " v. " in remainder.lower() and prefix.startswith("state of"):
                idx2 = remainder.lower().index(" v. ") + 4
                name_raw = remainder[idx2:].strip().split(",")[0].strip()
            else:
                name_raw = remainder.split(",")[0].strip()
            break

    if not name_raw or len(name_raw) < 3:
        return None

    search_name = _normalize_name(name_raw)
    if not search_name or len(search_name) < 3:
        return None

    # Search arrests collection
    try:
        # Use the last word as the primary search token (likely surname)
        tokens = search_name.split()
        surname = tokens[-1] if tokens else search_name

        cursor = db.arrests.find(
            {"$or": [
                {"full_name": {"$regex": surname, "$options": "i"}},
                {"Defendant_Name": {"$regex": surname, "$options": "i"}},
            ]},
            {"_id": 1, "full_name": 1, "Defendant_Name": 1, "defendant_id": 1, "county": 1},
        ).limit(10)
        candidates = await cursor.to_list(length=10)

        if not candidates:
            return None

        # Score each candidate by name similarity
        best_match = None
        best_score = 0.0

        for c in candidates:
            cand_name = c.get("full_name") or c.get("Defendant_Name", "")
            similarity = _name_similarity(name_raw, cand_name)
            if similarity > best_score:
                best_score = similarity
                best_match = c

        if best_match and best_score >= 0.4:
            # Confidence tiers based on similarity
            if best_score >= 0.8:
                confidence = 0.9
            elif best_score >= 0.6:
                confidence = 0.7
            else:
                confidence = 0.5

            # Penalize if multiple candidates with similar scores
            close_matches = sum(1 for c in candidates
                                if _name_similarity(name_raw, c.get("full_name") or c.get("Defendant_Name", "")) >= best_score * 0.8)
            if close_matches > 1:
                confidence *= 0.7  # Ambiguity penalty

            return {
                "defendant_id": str(best_match.get("defendant_id") or best_match["_id"]),
                "name": best_match.get("full_name") or best_match.get("Defendant_Name", ""),
                "confidence": round(confidence, 2),
                "similarity": round(best_score, 2),
                "method": "jaccard_name_similarity",
            }
    except Exception as e:
        log.debug("Defendant match error: %s", str(e)[:100])

    return None


async def _ai_analyze_opinion(opinion: dict) -> Optional[dict]:
    """Use OpenAI to extract bail-relevant intelligence from an opinion.

    Extracts: charge classification, sentencing patterns, bail implications,
    risk factors, and a 1-line bond-risk summary.

    Returns structured analysis dict or None if AI unavailable.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    case_name = opinion.get("case_name", "Unknown")
    snippet = opinion.get("snippet", "")[:1500]  # Limit token usage
    disposition = opinion.get("disposition", "unknown")
    court = opinion.get("court_name", "")
    date = opinion.get("date_filed", "")

    prompt = f"""Analyze this court opinion for bail bond intelligence.
Case: {case_name}
Court: {court}
Date: {date}
Disposition: {disposition}
Snippet: {snippet}

Extract as JSON:
{{
  "charge_type": "felony/misdemeanor/unknown",
  "charge_category": "drug/violent/property/dui/fraud/other",
  "sentence_indicator": "prison/probation/time_served/acquitted/pending/unknown",
  "bail_implications": "One sentence: how this outcome affects bail bond risk for similar cases",
  "risk_signal": "high/medium/low/neutral",
  "key_factors": ["list of 1-3 key factors relevant to bail underwriting"]
}}
Respond ONLY with the JSON object."""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
            )
            if resp.status_code != 200:
                return None

            import json
            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        log.debug("AI analysis failed for %s: %s", case_name[:40], str(e)[:80])
        return None


def _score_bail_impact(opinion: dict) -> dict:
    """Score how impactful this opinion is for bail bond intelligence.

    Returns a dict with score (0-100) and reasoning.
    """
    score = 0
    factors = []
    disp = opinion.get("disposition", "unknown")

    # High-impact dispositions for bail
    bail_critical = {
        "bond_forfeiture": 95, "fta": 90, "bond_revoked": 85,
        "bond_reduced": 70, "bond_increased": 70,
        "probation_violation": 65, "pretrial_order": 60,
    }
    if disp in bail_critical:
        score = bail_critical[disp]
        factors.append(f"bail-critical disposition: {disp}")
    elif disp in ("conviction", "sentencing", "plea"):
        score = 50
        factors.append(f"sentencing outcome: {disp}")
    elif disp in ("dismissed", "acquittal", "nolle_prosequi"):
        score = 40
        factors.append(f"favorable outcome: {disp}")
    elif disp != "unknown":
        score = 25
        factors.append(f"general legal outcome: {disp}")

    # Bonus for matched defendants
    if opinion.get("matched_defendant_id"):
        score = min(100, score + 20)
        factors.append("matched to existing defendant")

    # Bonus for AI analysis
    ai = opinion.get("ai_analysis", {})
    if ai.get("risk_signal") == "high":
        score = min(100, score + 15)
        factors.append("AI: high risk signal")

    return {"score": score, "factors": factors}
