# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""ShamrockLeads — Agency Reports API Blueprint

Endpoints:
  GET /api/reports/discharged          — Exonerated & surrendered bonds
  GET /api/reports/surety-liability    — Per-surety financial statement
  GET /api/reports/voided-powers       — Voided POAs (manual removal)
  GET /api/reports/expired-powers      — Expired POAs (semi-annual expiration)
  GET /api/reports/forfeitures         — Forfeited bonds (compliance)
  GET /api/reports/agent-production    — Per-agent bond production
  GET /api/reports/check-in-compliance — Missed check-ins / overdue
  GET /api/reports/poa-inventory       — POA counts by surety, tier, status

All routes use Quart (async) + Motor (async MongoDB).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

reports_bp = APIRouter(prefix="/api", tags=["reports"])
# ── Agent registry — licensed agents with their full names ──────────────────
AGENTS = [
    {"id": "brendan", "name": "Brendan O'Neal"},
    {"id": "jason", "name": "Jason Taylor"},
]

# Surety financial rates per $100 in premium
SURETY_RATES = {
    "OSI": {"surety_per_100": 7.50, "buf_per_100": 5.00},
    "PALMETTO": {"surety_per_100": 10.00, "buf_per_100": 5.00},
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(s: str | None) -> datetime | None:
    """Parse YYYY-MM-DD string to UTC datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _date_filter(field: str = "bond_date") -> dict:
    """Build a MongoDB date range filter from query params."""
    start = _parse_date(_qp.get("start_date"))
    end = _parse_date(_qp.get("end_date"))
    if not start and not end:
        return {}
    f = {}
    if start:
        f["$gte"] = start.isoformat()
    if end:
        # End of day
        f["$lte"] = (end + timedelta(days=1)).isoformat()
    return {field: f} if f else {}


def _serialize_doc(doc: dict) -> dict:
    """Remove _id and convert datetimes for JSON."""
    doc.pop("_id", None)
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


def _calc_surety_split(bond_amount: float, surety: str, premium_rate: float = 0.10) -> dict:
    """Calculate premium split using surety rates."""
    s = surety.upper() if surety else "OSI"
    rates = SURETY_RATES.get(s, SURETY_RATES["OSI"])
    premium = bond_amount * premium_rate
    surety_owed = premium * (rates["surety_per_100"] / 100.0)
    buf_owed = premium * (rates["buf_per_100"] / 100.0)
    agent_retains = premium - surety_owed - buf_owed
    return {
        "premium": round(premium, 2),
        "surety_owed": round(surety_owed, 2),
        "buf_owed": round(buf_owed, 2),
        "agent_retains": round(agent_retains, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISCHARGED BONDS (exonerated / surrendered)
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/discharged")
async def discharged_bonds():
    """Bonds with status 'exonerated' or 'surrendered'."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {"status": {"$in": ["exonerated", "surrendered"]}}
        query.update(_date_filter("bond_date"))

        docs = await col.find(query, {"_id": 0}).sort("bond_date", -1).to_list(500)
        for d in docs:
            _serialize_doc(d)
            # Add surety split calculation
            ba = float(d.get("bond_amount", 0) or 0)
            s = d.get("surety") or d.get("insurance_company") or "OSI"
            d["split"] = _calc_surety_split(ba, s)

        # Summary
        total_bond = sum(float(d.get("bond_amount", 0) or 0) for d in docs)
        total_premium = sum(d.get("split", {}).get("premium", 0) for d in docs)
        exonerated = [d for d in docs if d.get("status") == "exonerated"]
        surrendered = [d for d in docs if d.get("status") == "surrendered"]

        return {
            "success": True,
            "bonds": docs,
            "records": docs,
            "count": len(docs),
            "exonerated_count": len(exonerated),
            "surrendered_count": len(surrendered),
            "total_bond_amount": round(total_bond, 2),
            "total_premium": round(total_premium, 2),
        }
    except Exception as exc:
        logger.exception("reports/discharged error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# SURETY LIABILITY STATEMENT
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/surety-liability")
async def surety_liability():
    """Per-surety financial breakdown: bond amounts, premium, surety owed, BUF, agent retains."""
    _qp = dict(request.query_params)
    try:
        db = get_db()
        col = db["active_bonds"]
        surety_filter = _qp.get("surety", "").strip().upper()

        # Build query — all non-voided bonds
        query = {"status": {"$nin": ["voided"]}}
        if surety_filter:
            query["$or"] = [
                {"surety": surety_filter},
                {"insurance_company": {"$regex": surety_filter, "$options": "i"}},
            ]
        query.update(_date_filter("bond_date"))

        docs = await col.find(query, {"_id": 0}).sort("bond_date", -1).to_list(1000)

        # Group by surety
        surety_groups = {}
        for d in docs:
            _serialize_doc(d)
            s = (d.get("surety") or d.get("insurance_company") or "OSI").upper()
            # Normalize surety name
            if "PALMETTO" in s:
                s = "PALMETTO"
            elif "OSI" in s or "SHAUGHNAHILL" in s.upper():
                s = "OSI"

            ba = float(d.get("bond_amount", 0) or 0)
            split = _calc_surety_split(ba, s)
            d["split"] = split

            if s not in surety_groups:
                surety_groups[s] = {
                    "surety": s,
                    "bond_count": 0,
                    "total_bond_amount": 0.0,
                    "total_premium": 0.0,
                    "total_surety_owed": 0.0,
                    "total_buf_owed": 0.0,
                    "total_agent_retains": 0.0,
                    "bonds": [],
                }

            g = surety_groups[s]
            g["bond_count"] += 1
            g["total_bond_amount"] += ba
            g["total_premium"] += split["premium"]
            g["total_surety_owed"] += split["surety_owed"]
            g["total_buf_owed"] += split["buf_owed"]
            g["total_agent_retains"] += split["agent_retains"]
            g["bonds"].append({
                "defendant_name": d.get("defendant_name", ""),
                "booking_number": d.get("booking_number", ""),
                "county": d.get("county", ""),
                "bond_amount": ba,
                "bond_date": d.get("bond_date", ""),
                "status": d.get("status", ""),
                "case_number": d.get("case_number", ""),
                "agent_name": d.get("agent_name", ""),
                **split,
            })

        # Round totals
        for g in surety_groups.values():
            for k in ("total_bond_amount", "total_premium", "total_surety_owed",
                       "total_buf_owed", "total_agent_retains"):
                g[k] = round(g[k], 2)

        # Grand totals
        grand = {
            "total_bonds": sum(g["bond_count"] for g in surety_groups.values()),
            "total_bond_amount": round(sum(g["total_bond_amount"] for g in surety_groups.values()), 2),
            "total_premium": round(sum(g["total_premium"] for g in surety_groups.values()), 2),
            "total_surety_owed": round(sum(g["total_surety_owed"] for g in surety_groups.values()), 2),
            "total_buf_owed": round(sum(g["total_buf_owed"] for g in surety_groups.values()), 2),
            "total_agent_retains": round(sum(g["total_agent_retains"] for g in surety_groups.values()), 2),
        }

        return {
            "success": True,
            "sureties": list(surety_groups.values()),
            "grand_totals": grand,
        }
    except Exception as exc:
        logger.exception("reports/surety-liability error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# VOIDED POWERS
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/voided-powers")
async def voided_powers():
    """POAs that were manually voided."""
    _qp = dict(request.query_params)
    try:
        db = get_db()
        col = db["poa_inventory"]
        query = {"status": "voided"}
        surety_filter = _qp.get("surety", "").strip().lower()
        if surety_filter:
            query["surety_id"] = surety_filter

        docs = await col.find(query, {"_id": 0}).sort("voided_at", -1).to_list(500)
        for d in docs:
            _serialize_doc(d)

        return {
            "success": True,
            "powers": docs,
            "records": docs,
            "count": len(docs),
        }
    except Exception as exc:
        logger.exception("reports/voided-powers error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED POWERS (semi-annual expiration)
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/expired-powers")
async def expired_powers():
    """POAs past their expiration date (semi-annual cycle)."""
    _qp = dict(request.query_params)
    try:
        db = get_db()
        col = db["poa_inventory"]
        now_iso = _utc_now().isoformat()

        query = {
            "expiration": {"$ne": None, "$lt": now_iso},
            "status": {"$nin": ["voided"]},  # Don't double-count voided ones
        }
        surety_filter = _qp.get("surety", "").strip().lower()
        if surety_filter:
            query["surety_id"] = surety_filter

        docs = await col.find(query, {"_id": 0}).sort("expiration", 1).to_list(500)
        for d in docs:
            _serialize_doc(d)

        # Also show POAs expiring within 30 days (upcoming)
        cutoff_30 = (_utc_now() + timedelta(days=30)).isoformat()
        upcoming_query = {
            "expiration": {"$gte": now_iso, "$lte": cutoff_30},
            "status": {"$nin": ["voided"]},
        }
        if surety_filter:
            upcoming_query["surety_id"] = surety_filter

        upcoming = await col.find(upcoming_query, {"_id": 0}).sort("expiration", 1).to_list(200)
        for d in upcoming:
            _serialize_doc(d)

        return {
            "success": True,
            "expired": docs,
            "expired_count": len(docs),
            "expiring_soon": upcoming,
            "expiring_soon_count": len(upcoming),
        }
    except Exception as exc:
        logger.exception("reports/expired-powers error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# FORFEITURES
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/forfeitures")
async def forfeitures():
    """Bonds with status 'forfeited'."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {"status": "forfeited"}
        query.update(_date_filter("bond_date"))

        docs = await col.find(query, {"_id": 0}).sort("bond_date", -1).to_list(500)
        total_liability = 0.0
        for d in docs:
            _serialize_doc(d)
            ba = float(d.get("bond_amount", 0) or 0)
            total_liability += ba
            d["split"] = _calc_surety_split(ba, d.get("surety") or d.get("insurance_company") or "OSI")

        avg_bond = round(total_liability / max(len(docs), 1), 2)

        return {
            "success": True,
            "bonds": docs,
            "records": docs,
            "count": len(docs),
            "total_liability": round(total_liability, 2),
            "avg_bond_amount": avg_bond,
        }
    except Exception as exc:
        logger.exception("reports/forfeitures error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# AGENT PRODUCTION
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/agent-production")
async def agent_production():
    """Per-agent bond count, premium, avg bond, surety breakdown, production metrics."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {}
        query.update(_date_filter("bond_date"))

        # Normalize legacy short names → full names so they group correctly.
        # Old records may have "Brendan" instead of "Brendan O'Neal".
        AGENT_ALIAS = {
            "Brendan": "Brendan O'Neal",
            "brendan": "Brendan O'Neal",
            "Jason": "Jason Taylor",
            "jason": "Jason Taylor",
        }

        pipe = [
            {"$match": query} if query else {"$match": {}},
            # Normalize agent_name (short → full)
            {"$addFields": {
                "agent_name_norm": {
                    "$switch": {
                        "branches": [
                            {"case": {"$eq": ["$agent_name", alias]},
                             "then": full}
                            for alias, full in AGENT_ALIAS.items()
                        ],
                        "default": {"$ifNull": ["$agent_name", "Unassigned"]},
                    }
                },
            }},
            # Per-agent + per-surety breakdown
            {"$group": {
                "_id": {
                    "agent": "$agent_name_norm",
                    "surety": {"$toUpper": {"$ifNull": ["$insurance_company", "UNKNOWN"]}},
                },
                "bond_count": {"$sum": 1},
                "total_bond_amount": {"$sum": "$bond_amount"},
                "total_premium": {"$sum": "$premium"},
                "counties": {"$addToSet": "$county"},
            }},
            {"$sort": {"_id.agent": 1, "_id.surety": 1}},
        ]

        raw = await col.aggregate(pipe).to_list(None)

        # Re-group by agent, accumulating surety breakdown
        agent_map = {}
        for r in raw:
            name = r["_id"]["agent"] or "Unassigned"
            surety = r["_id"]["surety"] or "UNKNOWN"
            if name not in agent_map:
                agent_map[name] = {
                    "agent_name": name,
                    "bond_count": 0,
                    "total_bond_amount": 0.0,
                    "total_premium": 0.0,
                    "counties": set(),
                    "by_surety": {},
                }
            a = agent_map[name]
            a["bond_count"] += r["bond_count"]
            a["total_bond_amount"] += r["total_bond_amount"] or 0
            a["total_premium"] += r["total_premium"] or 0
            a["counties"].update(r.get("counties", []))
            a["by_surety"][surety] = a["by_surety"].get(surety, 0) + r["bond_count"]

        agents = []
        for a in sorted(agent_map.values(), key=lambda x: x["total_premium"], reverse=True):
            bc = a["bond_count"]
            agents.append({
                "agent_name": a["agent_name"],
                "bond_count": bc,
                "total_bond_amount": round(a["total_bond_amount"], 2),
                "total_premium": round(a["total_premium"], 2),
                "avg_bond": round(a["total_bond_amount"] / bc, 2) if bc else 0,
                "avg_premium": round(a["total_premium"] / bc, 2) if bc else 0,
                "counties": sorted(a["counties"] - {"", None}),
                "county_count": len(a["counties"] - {"", None}),
                "by_surety": a["by_surety"],
            })

        # Grand totals
        grand = {
            "total_bonds": sum(a["bond_count"] for a in agents),
            "total_premium": round(sum(a["total_premium"] for a in agents), 2),
            "total_bond_amount": round(sum(a["total_bond_amount"] for a in agents), 2),
            "avg_bond_amount": round(
                sum(a["total_bond_amount"] for a in agents) /
                max(sum(a["bond_count"] for a in agents), 1), 2
            ),
        }

        # Include registered agent list
        return {
            "success": True,
            "agents": agents,
            "registered_agents": AGENTS,
            "grand_totals": grand,
        }
    except Exception as exc:
        logger.exception("reports/agent-production error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# CHECK-IN COMPLIANCE
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/check-in-compliance")
async def check_in_compliance():
    """Active bonds sorted by missed check-ins, overdue status."""
    try:
        db = get_db()
        col = db["active_bonds"]
        now = _utc_now()
        now_iso = now.isoformat()

        # Only active/monitoring/alert bonds
        query = {"status": {"$in": ["active", "monitoring", "alert"]}}

        docs = await col.find(query, {"_id": 0}).sort("missed_check_ins", -1).to_list(500)

        compliant = 0
        overdue = 0
        total = len(docs)

        for d in docs:
            _serialize_doc(d)
            missed = d.get("missed_check_ins", 0)
            next_due = d.get("next_check_in_due", "")

            is_overdue = False
            if next_due and next_due < now_iso:
                is_overdue = True

            d["is_overdue"] = is_overdue
            d["compliance_status"] = "overdue" if is_overdue else ("warning" if missed > 0 else "compliant")

            if is_overdue:
                overdue += 1
            elif missed == 0:
                compliant += 1

        compliance_rate = round((compliant / total * 100), 1) if total > 0 else 100.0

        return {
            "success": True,
            "records": docs,
            "count": total,
            "compliant": compliant,
            "overdue": overdue,
            "compliance_rate": compliance_rate,
        }
    except Exception as exc:
        logger.exception("reports/check-in-compliance error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────────────────────
# POA INVENTORY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/poa-inventory")
async def poa_inventory_summary():
    """POA counts grouped by surety, tier/prefix, and status."""
    try:
        db = get_db()
        col = db["poa_inventory"]
        now_iso = _utc_now().isoformat()

        pipe = [
            {"$group": {
                "_id": {
                    "surety_id": "$surety_id",
                    "poa_prefix": "$poa_prefix",
                    "status": "$status",
                },
                "count": {"$sum": 1},
                "max_bond_value": {"$max": "$max_bond_value"},
            }},
            {"$sort": {"_id.surety_id": 1, "_id.poa_prefix": 1, "_id.status": 1}},
        ]

        results = await col.aggregate(pipe).to_list(None)

        # Restructure into surety → prefix → status breakdown
        by_surety = {}
        for r in results:
            sid = r["_id"]["surety_id"] or "unknown"
            prefix = r["_id"]["poa_prefix"] or "unknown"
            status = r["_id"]["status"] or "unknown"
            count = r["count"]
            max_val = r.get("max_bond_value", 0) or 0

            if sid not in by_surety:
                by_surety[sid] = {"surety_id": sid, "tiers": {}, "totals": {}}

            if prefix not in by_surety[sid]["tiers"]:
                by_surety[sid]["tiers"][prefix] = {
                    "prefix": prefix,
                    "max_bond_value": max_val,
                    "statuses": {},
                }

            by_surety[sid]["tiers"][prefix]["statuses"][status] = count

            # Running totals per surety
            by_surety[sid]["totals"][status] = by_surety[sid]["totals"].get(status, 0) + count

        # Count expired (across all)
        expired_count = await col.count_documents({
            "expiration": {"$ne": None, "$lt": now_iso},
            "status": {"$nin": ["voided"]},
        })

        # Convert tiers dict to sorted list
        for sid in by_surety:
            by_surety[sid]["tiers"] = sorted(
                by_surety[sid]["tiers"].values(),
                key=lambda t: t.get("max_bond_value", 0)
            )

        return {
            "success": True,
            "sureties": list(by_surety.values()),
            "expired_count": expired_count,
        }
    except Exception as exc:
        logger.exception("reports/poa-inventory error: %s", exc)
        return {"success": False, "error": str(exc)}, 500


@reports_bp.get("/reports/kpi-trends")
async def kpi_trends():
    """Return period-over-period KPI comparison for the Reports tab trend indicators."""
    _qp = dict(request.query_params)
    try:
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        period_days = int(_qp.get("days", 30))
        cur_start = (now - timedelta(days=period_days)).isoformat()
        prev_start = (now - timedelta(days=period_days * 2)).isoformat()
        prev_end = cur_start

        db = get_db()
        active_col = db["active_bonds"]
        poa_col = db["poa_inventory"]

        async def _count(col, query):
            return await col.count_documents(query)

        async def _sum_field(col, field, query):
            pipe = [{"$match": query}, {"$group": {"_id": None, "total": {"$sum": f"${field}"}}}]
            res = await col.aggregate(pipe).to_list(1)
            return (res[0]["total"] if res else 0) or 0

        cur_bonds  = await _count(active_col, {"created_at": {"$gte": cur_start}})
        prev_bonds = await _count(active_col, {"created_at": {"$gte": prev_start, "$lt": prev_end}})

        cur_disc  = await _count(active_col, {"status": {"$in": ["discharged", "exonerated"]}, "discharged_at": {"$gte": cur_start}})
        prev_disc = await _count(active_col, {"status": {"$in": ["discharged", "exonerated"]}, "discharged_at": {"$gte": prev_start, "$lt": prev_end}})

        cur_liab  = await _sum_field(active_col, "bond_amount", {"status": "active"})
        prev_liab = await _sum_field(active_col, "bond_amount", {"status": "active", "created_at": {"$lt": cur_start}})

        cur_poa  = await _count(poa_col, {"status": "used", "used_at": {"$gte": cur_start}})
        prev_poa = await _count(poa_col, {"status": "used", "used_at": {"$gte": prev_start, "$lt": prev_end}})

        def _pct(cur, prev):
            if prev == 0:
                return None
            return round((cur - prev) / prev * 100, 1)

        return {
            "success": True,
            "period_days": period_days,
            "bonds":            {"current": cur_bonds,  "prior": prev_bonds, "pct_change": _pct(cur_bonds,  prev_bonds)},
            "discharged":       {"current": cur_disc,   "prior": prev_disc,  "pct_change": _pct(cur_disc,   prev_disc)},
            "surety_liability": {"current": cur_liab,   "prior": prev_liab,  "pct_change": _pct(cur_liab,   prev_liab)},
            "poa_used":         {"current": cur_poa,    "prior": prev_poa,   "pct_change": _pct(cur_poa,    prev_poa)},
        }
    except Exception as exc:
        logger.exception("reports/kpi-trends error: %s", exc)
        return {"success": False, "error": str(exc)}, 500
