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
from datetime import datetime, timezone
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
    from dashboard.services.federal_bond_risk import (
        DEFAULT_PAUSE_S,
        MAX_REQUESTS_PER_RUN,
        build_courtlistener_request_gate,
    )

    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    client = CourtListenerClient(api_token=token if token else None)
    client.set_request_gate(
        build_courtlistener_request_gate(
            db,
            client=client,
            pause_s=DEFAULT_PAUSE_S,
            run_limit=MAX_REQUESTS_PER_RUN,
        )
    )

    try:
        opinions = await client.ingest_recent_opinions(
            days_back=days_back, states=states, page_size=20,
            bail_relevant_only=True,
        )
        ingest_status = getattr(client, "_last_ingest_status", None) or {}
        quota_exhausted = bool(ingest_status.get("quota_exhausted"))
        failed_states = list(ingest_status.get("failed_states") or [])
        states_completed = list(ingest_status.get("states_completed") or [])
        states_skipped_quota = list(ingest_status.get("states_skipped_quota") or [])
        maintenance = bool(ingest_status.get("maintenance"))

        if not opinions:
            if quota_exhausted:
                return {
                    "success": False,
                    "skipped": "quota_exhausted",
                    "incomplete_coverage": True,
                    "error": "CourtListener request quota reached before opinion ingestion completed",
                    "ingested": 0,
                    "duplicates": 0,
                    "total_fetched": 0,
                    "states_queried": states or "all_se_us",
                    "states_completed": states_completed,
                    "states_skipped_quota": states_skipped_quota,
                    "failed_states": failed_states,
                    "api_health": client.get_api_health(),
                }
            if failed_states:
                return {
                    "success": False,
                    "incomplete_coverage": True,
                    "error": f"CourtListener opinion ingestion failed for states: {', '.join(failed_states)}",
                    "ingested": 0,
                    "duplicates": 0,
                    "total_fetched": 0,
                    "states_queried": states or "all_se_us",
                    "states_completed": states_completed,
                    "states_skipped_quota": states_skipped_quota,
                    "failed_states": failed_states,
                    "api_health": client.get_api_health(),
                }
            if maintenance:
                return {
                    "success": True,
                    "skipped": "maintenance",
                    "incomplete_coverage": False,
                    "ingested": 0,
                    "duplicates": 0,
                    "message": "FLP maintenance window active",
                    "api_health": client.get_api_health(),
                }
            return {
                "success": True,
                "incomplete_coverage": False,
                "ingested": 0,
                "duplicates": 0,
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

        if quota_exhausted:
            log.warning(
                "Ingestion stopped early on quota exhaustion: %d inserted, %d dupes, completed=%s, skipped=%s",
                inserted, duplicates, states_completed, states_skipped_quota,
            )
            return {
                "success": False,
                "skipped": "quota_exhausted",
                "incomplete_coverage": True,
                "error": "CourtListener request quota reached during opinion ingestion",
                "ingested": inserted,
                "duplicates": duplicates,
                "ai_analyzed": ai_analyzed,
                "total_fetched": len(opinions),
                "states_queried": states or "all_se_us",
                "states_completed": states_completed,
                "states_skipped_quota": states_skipped_quota,
                "failed_states": failed_states,
                "completed_at": datetime.now(timezone.utc).isoformat() + "Z",
                "api_health": client.get_api_health(),
            }

        if failed_states:
            log.warning(
                "Ingestion incomplete across states %s: %d inserted, %d dupes",
                failed_states, inserted, duplicates,
            )
            return {
                "success": False,
                "incomplete_coverage": True,
                "error": f"CourtListener opinion ingestion incomplete; failed states: {', '.join(failed_states)}",
                "ingested": inserted,
                "duplicates": duplicates,
                "ai_analyzed": ai_analyzed,
                "total_fetched": len(opinions),
                "states_queried": states or "all_se_us",
                "states_completed": states_completed,
                "states_skipped_quota": states_skipped_quota,
                "failed_states": failed_states,
                "completed_at": datetime.now(timezone.utc).isoformat() + "Z",
                "api_health": client.get_api_health(),
            }

        log.info(
            "Ingestion complete: %d inserted, %d dupes, %d AI-analyzed",
            inserted, duplicates, ai_analyzed,
        )

        return {
            "success": True,
            "incomplete_coverage": False,
            "ingested": inserted,
            "duplicates": duplicates,
            "ai_analyzed": ai_analyzed,
            "total_fetched": len(opinions),
            "states_queried": states or "all_se_us",
            "states_completed": states_completed,
            "days_back": days_back,
            "completed_at": datetime.now(timezone.utc).isoformat() + "Z",
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
    """Score how impactful this opinion is for bail bond intelligence."""
    from dashboard.services.courtlistener_client import score_bail_impact
    return score_bail_impact(opinion)


async def save_court_case(db, payload: dict) -> dict:
    """Save/upsert a CourtListener search result or dossier into `court_outcomes`."""
    from dashboard.services.courtlistener_client import (
        CourtListenerClient,
        classify_disposition_category,
        score_bail_impact,
    )

    source_id = str(
        payload.get("source_id")
        or payload.get("cluster_id")
        or payload.get("id")
        or ""
    ).strip()
    if not source_id:
        return {"success": False, "error": "source_id or cluster_id is required"}

    case_name = (payload.get("case_name") or payload.get("caseName") or "").strip()
    if not case_name:
        return {"success": False, "error": "case_name is required"}

    snippet = (payload.get("snippet") or "")[:500]
    disposition = (payload.get("disposition") or "").strip().lower()
    if not disposition:
        disposition = CourtListenerClient._classify_disposition(snippet, case_name)

    doc = {
        "source": "courtlistener",
        "source_id": source_id,
        "cluster_id": payload.get("cluster_id") or source_id,
        "docket_id": payload.get("docket_id") or "",
        "court_id": payload.get("court_id") or "",
        "court_name": payload.get("court_name") or payload.get("court_id") or "",
        "state": (payload.get("state") or "").upper(),
        "case_name": case_name,
        "docket_number": payload.get("docket_number") or payload.get("docketNumber") or "",
        "date_filed": payload.get("date_filed") or payload.get("dateFiled") or "",
        "disposition": disposition,
        "disposition_category": classify_disposition_category(disposition),
        "snippet": snippet,
        "judges": payload.get("judges") or payload.get("judge") or "",
        "assigned_to_str": payload.get("assigned_to_str") or "",
        "referred_to_str": payload.get("referred_to_str") or "",
        "citation": payload.get("citation") or [],
        "citation_count": int(payload.get("citation_count") or 0),
        "absolute_url": payload.get("absolute_url") or "",
        "suit_nature": payload.get("suit_nature") or "",
        "court_type": payload.get("court_type") or "state",
        "court_tier": payload.get("court_tier") or "unknown",
        "jurisdiction": (payload.get("state") or "").upper(),
        "ingested_at": datetime.now(timezone.utc).isoformat() + "Z",
    }

    match_result = await _fuzzy_match_defendant(db, case_name)
    if match_result:
        doc["matched_defendant_id"] = match_result.get("defendant_id")
        doc["matched_defendant_name"] = match_result.get("name")
        doc["match_confidence"] = match_result.get("confidence", 0)
        doc["match_method"] = match_result.get("method", "name")

    doc["bail_impact"] = score_bail_impact(doc)

    collection = db["court_outcomes"]
    await collection.update_one(
        {"source": "courtlistener", "source_id": source_id},
        {"$set": doc},
        upsert=True,
    )
    saved = await collection.find_one({"source": "courtlistener", "source_id": source_id})
    if saved and "_id" in saved:
        saved["_id"] = str(saved["_id"])
    return {"success": True, "case": saved or doc}


async def get_court_intel_analytics(db, state: Optional[str] = None) -> dict:
    """Compute comprehensive bail-intelligence analytics from `court_outcomes`."""
    from dashboard.services.courtlistener_client import (
        BAIL_SEARCH_PRESETS,
        classify_disposition_category,
    )

    collection = db["court_outcomes"]
    query_filter: dict = {}
    if state and state.strip():
        query_filter["state"] = state.strip().upper()

    presets_list = [{"key": k, **v} for k, v in BAIL_SEARCH_PRESETS.items()]
    total = await collection.count_documents(query_filter)

    if total == 0:
        return {
            "success": True,
            "state": state.upper() if state else None,
            "kpi": {
                "total_outcomes": 0,
                "bail_critical_count": 0,
                "bail_critical_pct": 0.0,
                "avg_bail_impact": 0.0,
                "matched_defendants": 0,
                "active_states": 0,
                "unique_courts": 0,
                "unique_judges": 0,
                "last_ingestion": None,
            },
            "by_disposition": [],
            "by_state": [],
            "by_court": [],
            "by_judge": [],
            "monthly_trend": [],
            "top_precedents": [],
            "presets": presets_list,
        }

    projection = {
        "source_id": 1,
        "cluster_id": 1,
        "court_id": 1,
        "court_name": 1,
        "state": 1,
        "case_name": 1,
        "docket_number": 1,
        "date_filed": 1,
        "disposition": 1,
        "judges": 1,
        "assigned_to_str": 1,
        "citation_count": 1,
        "bail_impact": 1,
        "matched_defendant_id": 1,
        "ingested_at": 1,
        "absolute_url": 1,
    }
    docs = await (
        collection.find(query_filter, projection)
        .sort([("ingested_at", -1), ("date_filed", -1)])
        .limit(2500)
        .to_list(length=2500)
    )

    disp_counts: dict[str, int] = {}
    state_agg: dict[str, dict] = {}
    court_agg: dict[str, dict] = {}
    judge_agg: dict[str, dict] = {}
    month_agg: dict[str, dict] = {}

    bail_critical_count = 0
    impact_scores: list[float] = []
    matched_count = 0
    last_ingestion: Optional[str] = None

    for d in docs:
        disp = (d.get("disposition") or "unknown").lower()
        cat = classify_disposition_category(disp)
        disp_counts[disp] = disp_counts.get(disp, 0) + 1
        if cat == "bail_critical":
            bail_critical_count += 1

        score = float((d.get("bail_impact") or {}).get("score") or 0)
        impact_scores.append(score)

        if d.get("matched_defendant_id"):
            matched_count += 1

        ing_at = d.get("ingested_at")
        if ing_at and (last_ingestion is None or ing_at > last_ingestion):
            last_ingestion = ing_at

        st = (d.get("state") or "UNKNOWN").upper()
        s_bucket = state_agg.setdefault(
            st, {"state": st, "count": 0, "bail_critical": 0, "_score_sum": 0.0}
        )
        s_bucket["count"] += 1
        s_bucket["_score_sum"] += score
        if cat == "bail_critical":
            s_bucket["bail_critical"] += 1

        cid = d.get("court_id") or d.get("court_name") or "unknown"
        cname = d.get("court_name") or cid
        c_bucket = court_agg.setdefault(
            cid,
            {
                "court_id": cid,
                "court_name": cname,
                "state": st,
                "count": 0,
                "bail_critical": 0,
                "forfeiture_fta_count": 0,
            },
        )
        c_bucket["count"] += 1
        if cat == "bail_critical":
            c_bucket["bail_critical"] += 1
        if disp in ("bond_forfeiture", "fta", "bond_revoked"):
            c_bucket["forfeiture_fta_count"] += 1

        raw_judges = d.get("judges") or d.get("assigned_to_str") or ""
        if raw_judges:
            for part in re.split(r"[,;]", str(raw_judges)):
                jname = part.strip()
                if not jname or len(jname) < 2:
                    continue
                j_bucket = judge_agg.setdefault(
                    jname,
                    {
                        "judge": jname,
                        "count": 0,
                        "bail_critical": 0,
                        "favorable": 0,
                        "adverse": 0,
                        "_score_sum": 0.0,
                    },
                )
                j_bucket["count"] += 1
                j_bucket["_score_sum"] += score
                if cat == "bail_critical":
                    j_bucket["bail_critical"] += 1
                elif cat == "favorable":
                    j_bucket["favorable"] += 1
                elif cat == "adverse":
                    j_bucket["adverse"] += 1

        dfiled = str(d.get("date_filed") or "")
        if len(dfiled) >= 7 and dfiled[4:5] == "-":
            ym = dfiled[:7]
            m_bucket = month_agg.setdefault(ym, {"month": ym, "total": 0, "bail_critical": 0})
            m_bucket["total"] += 1
            if cat == "bail_critical":
                m_bucket["bail_critical"] += 1

    sample_n = len(docs)
    by_disposition = [
        {
            "disposition": k,
            "count": v,
            "pct": round((v / sample_n) * 100, 1) if sample_n else 0.0,
            "category": classify_disposition_category(k),
        }
        for k, v in sorted(disp_counts.items(), key=lambda x: -x[1])
    ]

    by_state = [
        {
            "state": v["state"],
            "count": v["count"],
            "bail_critical": v["bail_critical"],
            "avg_impact": round(v["_score_sum"] / v["count"], 1) if v["count"] else 0.0,
        }
        for v in sorted(state_agg.values(), key=lambda x: -x["count"])
    ]

    by_court = sorted(court_agg.values(), key=lambda x: -x["count"])[:15]

    by_judge = [
        {
            "judge": v["judge"],
            "count": v["count"],
            "bail_critical": v["bail_critical"],
            "favorable": v["favorable"],
            "adverse": v["adverse"],
            "avg_impact": round(v["_score_sum"] / v["count"], 1) if v["count"] else 0.0,
        }
        for v in sorted(judge_agg.values(), key=lambda x: (-x["count"], -x["_score_sum"]))[:15]
    ]

    monthly_trend = sorted(month_agg.values(), key=lambda x: x["month"])[-12:]

    top_cursor = (
        collection.find(query_filter)
        .sort([("bail_impact.score", -1), ("citation_count", -1)])
        .limit(12)
    )
    top_docs = await top_cursor.to_list(length=12)
    top_precedents = []
    for p in top_docs:
        item = dict(p)
        if "_id" in item:
            item["_id"] = str(item["_id"])
        if item.get("snippet"):
            item["snippet"] = item["snippet"][:260]
        item.setdefault("disposition_category", classify_disposition_category(item.get("disposition", "")))
        top_precedents.append(item)

    return {
        "success": True,
        "state": state.strip().upper() if state and state.strip() else None,
        "kpi": {
            "total_outcomes": total,
            "bail_critical_count": bail_critical_count,
            "bail_critical_pct": round((bail_critical_count / sample_n) * 100, 1) if sample_n else 0.0,
            "avg_bail_impact": round(sum(impact_scores) / sample_n, 1) if sample_n else 0.0,
            "matched_defendants": matched_count,
            "active_states": len([s for s in state_agg if s != "UNKNOWN"]),
            "unique_courts": len(court_agg),
            "unique_judges": len(judge_agg),
            "last_ingestion": last_ingestion,
        },
        "by_disposition": by_disposition,
        "by_state": by_state,
        "by_court": by_court,
        "by_judge": by_judge,
        "monthly_trend": monthly_trend,
        "top_precedents": top_precedents,
        "presets": presets_list,
    }
