"""One definition for state-intel counts and the defendant list behind them.

A graphic that says N defendants and the list that opens from it must use
these clauses. Write / Print stays on the existing desk; this module only
decides who is in the population.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dashboard.extensions import ACTIVE_STATE_CODES

# Same bands as scoring/lead_scorer.py (Hot >= 70, Warm >= 40).
HOT_SCORE = 70
WARM_SCORE = 40

# status wins when it is set. custody_status is only the fallback.
CUSTODY_RX = "custody|confined|held|booked"

_FULL_NAMES = {
    "FL": ("FL", "fl", "Florida", "FLORIDA"),
    "GA": ("GA", "ga", "Ga", "Georgia", "GEORGIA"),
    "SC": ("SC", "sc", "Sc", "South Carolina", "SOUTH CAROLINA"),
    "NC": ("NC", "nc", "Nc", "North Carolina", "NORTH CAROLINA"),
    "TN": ("TN", "tn", "Tn", "Tennessee", "TENNESSEE"),
    "TX": ("TX", "tx", "Tx", "Texas", "TEXAS"),
    "LA": ("LA", "la", "La", "Louisiana", "LOUISIANA"),
    "AL": ("AL", "al", "Al", "Alabama", "ALABAMA"),
    "CT": ("CT", "ct", "Ct", "Connecticut", "CONNECTICUT"),
    "MS": ("MS", "ms", "Ms", "Mississippi", "MISSISSIPPI"),
    "OH": ("OH", "oh", "Oh", "Ohio", "OHIO"),
}

PRESETS = ("24h", "7d", "all", "hot", "warm", "range", "writable", "bond_ready", "custody")

# Strip $, commas, and spaces before converting. Scrapers store both numbers
# and "$1,250.00". A bare $convert turns the string form into 0.
SAFE_BOND: dict = {
    "$convert": {
        "input": {
            "$cond": [
                {"$eq": [{"$type": "$bond_amount"}, "string"]},
                {
                    "$replaceAll": {
                        "input": {
                            "$replaceAll": {
                                "input": {
                                    "$replaceAll": {
                                        "input": "$bond_amount",
                                        "find": "$",
                                        "replacement": "",
                                    }
                                },
                                "find": ",",
                                "replacement": "",
                            }
                        },
                        "find": " ",
                        "replacement": "",
                    }
                },
                "$bond_amount",
            ]
        },
        "to": "double",
        "onError": 0.0,
        "onNull": 0.0,
    }
}

_CUSTODY_STR: dict = {
    "$ifNull": ["$status", {"$ifNull": ["$custody_status", ""]}]
}


def state_values(state: str) -> tuple[str, ...]:
    code = (state or "").strip().upper()
    if code in _FULL_NAMES:
        return _FULL_NAMES[code]
    if not code:
        return ()
    return (code, code.lower(), code.title())


def state_clause(state: str) -> dict:
    """Match one state the same way the multi-state cards count it.

    Florida also owns legacy arrests that never stored a state.
    """
    code = (state or "").strip().upper()
    values = list(state_values(code))
    if code == "FL":
        return {"$or": [
            {"state": {"$in": values}},
            {"state": None},
            {"state": ""},
            {"state": {"$exists": False}},
        ]}
    return {"state": {"$in": values}}


def canonical_state(raw) -> str | None:
    """Fold a stored state into an active code. Blank legacy rows are Florida."""
    if raw is None or str(raw).strip() == "":
        return "FL"
    text = str(raw).strip()
    folded = text.casefold()
    for code in ACTIVE_STATE_CODES:
        if any(text == value or folded == value.casefold() for value in state_values(code)):
            return code
    return None


def time_clause(cutoff: datetime) -> dict:
    """Match scraped_at or created_at stored as datetimes or ISO strings."""
    iso = cutoff.isoformat()
    return {"$or": [
        {"scraped_at": {"$gte": cutoff}},
        {"scraped_at": {"$gte": iso}},
        {"created_at": {"$gte": cutoff}},
        {"created_at": {"$gte": iso}},
    ]}


def county_clause(county: str) -> dict:
    bare = re.sub(r"\s*\([A-Za-z]{2}\)\s*$", "", county or "").strip()
    return {"county": {"$regex": f"^{re.escape(bare)}(?:\\s+County)?$", "$options": "i"}}


def resolve_preset(
    preset: str,
    *,
    days: int | None = None,
    hours: int | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    min_bond: float | None = None,
) -> dict:
    """Return the effective filters for a graphic preset.

    Explicit days, hours, score, and min_bond override the preset defaults.
    """
    key = (preset or "all").strip().lower()
    if key not in PRESETS:
        raise ValueError(key)
    spec: dict = {"custody": False}
    if key == "24h":
        spec["hours"] = 24
    elif key == "7d":
        spec["days"] = 7
    elif key == "hot":
        spec["min_score"] = HOT_SCORE
    elif key == "warm":
        spec["min_score"] = WARM_SCORE
        spec["max_score"] = HOT_SCORE
    elif key == "range":
        spec["days"] = 30
    elif key == "writable":
        spec["days"] = 30
        spec["min_bond"] = 0.01
        spec["custody"] = True
    elif key == "bond_ready":
        spec["min_bond"] = 1000
        spec["min_score"] = WARM_SCORE
        spec["custody"] = True
    elif key == "custody":
        spec["custody"] = True
    if days is not None:
        spec["days"] = days
        spec.pop("hours", None)
    if hours is not None:
        spec["hours"] = hours
        spec.pop("days", None)
    if min_score is not None:
        spec["min_score"] = min_score
    if max_score is not None:
        spec["max_score"] = max_score
    if min_bond is not None:
        spec["min_bond"] = min_bond
    spec["preset"] = key
    return spec


def population_stages(
    spec: dict,
    *,
    state: str = "",
    county: str = "",
    q: str = "",
    now: datetime | None = None,
) -> list[dict]:
    """Aggregation stages shared by the count and the page of defendants."""
    now = now or datetime.now(timezone.utc)
    clauses: list[dict] = []
    if state:
        clauses.append(state_clause(state))
    if county:
        clauses.append(county_clause(county))
    if spec.get("hours"):
        clauses.append(time_clause(now - timedelta(hours=int(spec["hours"]))))
    elif spec.get("days"):
        clauses.append(time_clause(now - timedelta(days=int(spec["days"]))))
    min_score = spec.get("min_score")
    max_score = spec.get("max_score")
    if min_score is not None and max_score is not None:
        clauses.append({"lead_score": {"$gte": int(min_score), "$lt": int(max_score)}})
    elif min_score is not None:
        clauses.append({"lead_score": {"$gte": int(min_score)}})
    search = (q or "").strip()
    if search:
        escaped = re.escape(search)
        clauses.append({"$or": [
            {"full_name": {"$regex": escaped, "$options": "i"}},
            {"booking_number": {"$regex": escaped, "$options": "i"}},
        ]})

    stages: list[dict] = []
    if len(clauses) == 1:
        stages.append({"$match": clauses[0]})
    elif clauses:
        stages.append({"$match": {"$and": clauses}})
    stages.append({"$addFields": {"_safe_bond": SAFE_BOND, "_custody_str": _CUSTODY_STR}})
    post: dict = {}
    if spec.get("custody"):
        post["_custody_str"] = {"$regex": CUSTODY_RX, "$options": "i"}
    if spec.get("min_bond") is not None:
        post["_safe_bond"] = {"$gte": float(spec["min_bond"])}
    if post:
        stages.append({"$match": post})
    return stages


def parse_money(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def normalize_defendant(doc: dict) -> dict:
    charges = doc.get("charges") or ""
    if isinstance(charges, list):
        charges = " | ".join(str(c) for c in charges if c)
    scraped = doc.get("scraped_at") or doc.get("created_at") or ""
    if hasattr(scraped, "isoformat"):
        scraped = scraped.isoformat()
    booking = doc.get("booking_number")
    booking = "" if booking is None else str(booking)
    state = canonical_state(doc.get("state")) or (str(doc.get("state") or "").strip().upper()[:2])
    score = doc.get("lead_score") or 0
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    return {
        "full_name": doc.get("full_name") or "Unknown",
        "booking_number": booking,
        "county": doc.get("county") or "",
        "state": state,
        "charges": str(charges)[:180],
        "bond_amount": round(float(doc.get("_safe_bond") or 0), 2),
        "lead_score": score,
        "lead_status": doc.get("lead_status") or "",
        "status": doc.get("status") or doc.get("custody_status") or "",
        "scraped_at": scraped or "",
    }


def _sort_for(spec: dict) -> dict:
    preset = spec.get("preset")
    if preset == "bond_ready":
        return {"_safe_bond": -1, "lead_score": -1, "booking_number": 1}
    if preset == "writable":
        return {"lead_score": -1, "_safe_bond": -1, "booking_number": 1}
    return {"scraped_at": -1, "booking_number": 1}


_ROW_PROJECT = {
    "_id": 0,
    "full_name": 1,
    "booking_number": 1,
    "county": 1,
    "state": 1,
    "charges": 1,
    "lead_score": 1,
    "lead_status": 1,
    "status": 1,
    "custody_status": 1,
    "scraped_at": 1,
    "created_at": 1,
    "_safe_bond": 1,
}


async def fetch_population(
    arrests,
    spec: dict,
    *,
    state: str = "",
    county: str = "",
    q: str = "",
    page: int = 1,
    limit: int = 40,
    now: datetime | None = None,
) -> dict:
    """Return one page of defendants and the full population total."""
    page = max(1, int(page))
    limit = max(1, min(100, int(limit)))
    stages = population_stages(spec, state=state, county=county, q=q, now=now)
    pipeline = stages + [{
        "$facet": {
            "meta": [{"$count": "total"}],
            "sums": [{"$group": {
                "_id": None,
                "pipeline": {"$sum": "$_safe_bond"},
                "premium": {"$sum": {"$cond": [
                    {"$gt": ["$_safe_bond", 0]},
                    {"$max": [100.0, {"$multiply": ["$_safe_bond", 0.10]}]},
                    0.0,
                ]}},
            }}],
            "rows": [
                {"$sort": _sort_for(spec)},
                {"$skip": (page - 1) * limit},
                {"$limit": limit},
                {"$project": _ROW_PROJECT},
            ],
        }
    }]
    doc = None
    async for row in arrests.aggregate(pipeline, allowDiskUse=True):
        doc = row
        break
    total = 0
    pipeline_total = 0.0
    premium = 0.0
    rows: list[dict] = []
    if doc:
        meta = doc.get("meta") or []
        if meta:
            total = int(meta[0].get("total") or 0)
        sums = doc.get("sums") or []
        if sums:
            pipeline_total = float(sums[0].get("pipeline") or 0)
            premium = float(sums[0].get("premium") or 0)
        rows = [normalize_defendant(r) for r in (doc.get("rows") or [])]
    pages = max(1, (total + limit - 1) // limit) if total else 1
    return {
        "total": total,
        "page": page,
        "pages": pages,
        "limit": limit,
        "pipeline_total": round(pipeline_total, 2),
        "premium_estimate": round(premium, 2),
        "defendants": rows,
        "preset": spec.get("preset") or "",
        "filters": {
            "state": (state or "").upper(),
            "county": county or "",
            "days": spec.get("days") or 0,
            "hours": spec.get("hours") or 0,
            "min_score": spec.get("min_score"),
            "max_score": spec.get("max_score"),
            "min_bond": spec.get("min_bond"),
            "custody": bool(spec.get("custody")),
        },
    }


async def arrest_state_breakdown(arrests, now: datetime | None = None) -> dict:
    """Per-state totals for the command-center strip. Same fold as the cards."""
    now = now or datetime.now(timezone.utc)
    out = {
        code: {"total": 0, "last_24h": 0, "hot_leads": 0, "pipeline": 0.0}
        for code in ACTIVE_STATE_CODES
    }
    async for doc in arrests.aggregate([
        {"$addFields": {"_safe_bond": SAFE_BOND}},
        {"$group": {
            "_id": "$state",
            "total": {"$sum": 1},
            "hot": {"$sum": {"$cond": [
                {"$gte": [{"$ifNull": ["$lead_score", 0]}, HOT_SCORE]}, 1, 0,
            ]}},
            "pipeline": {"$sum": {"$cond": [
                {"$and": [
                    {"$gte": ["$_safe_bond", 1000]},
                    {"$gte": [{"$ifNull": ["$lead_score", 0]}, WARM_SCORE]},
                ]},
                "$_safe_bond",
                0,
            ]}},
        }},
    ], allowDiskUse=True):
        code = canonical_state(doc.get("_id"))
        if code not in out:
            continue
        out[code]["total"] += int(doc.get("total") or 0)
        out[code]["hot_leads"] += int(doc.get("hot") or 0)
        out[code]["pipeline"] = round(out[code]["pipeline"] + float(doc.get("pipeline") or 0), 2)
    async for doc in arrests.aggregate([
        {"$match": time_clause(now - timedelta(hours=24))},
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
    ], allowDiskUse=True):
        code = canonical_state(doc.get("_id"))
        if code in out:
            out[code]["last_24h"] += int(doc.get("count") or 0)
    return out
