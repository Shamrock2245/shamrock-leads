"""ShamrockLeads — FLDFS / DOI Compliance Reports API
=====================================================
Florida Department of Financial Services reporting endpoints.

Endpoints:
  GET /api/compliance/monthly-summary   — Monthly premium, liability, BUF
  GET /api/compliance/agent-1099        — Agent commission breakdown (1099-ready)
  GET /api/compliance/poa-utilization   — POA usage rates by surety/tier
  GET /api/compliance/forfeiture-log    — Estreature/forfeiture compliance log
  GET /api/compliance/full-filing       — Combined filing package (all above)

Query params: ?month=YYYY-MM  (defaults to current month)
              ?surety=osi|palmetto  (optional filter)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

fldfs_bp = Blueprint("fldfs_compliance", __name__)

# ── Surety financial rates ───────────────────────────────────────────────────
SURETY_RATES = {
    "OSI": {
        "full_name": "O'Shaughnahill Surety & Insurance Co.",
        "state": "FL", "city": "West Palm Beach",
        "surety_pct": 7.5, "buf_pct": 5.0,
        "license_number": "FL-OSI-2024",
    },
    "PALMETTO": {
        "full_name": "Palmetto Surety Corporation",
        "state": "Multi-State", "city": "Columbia, SC",
        "surety_pct": 10.0, "buf_pct": 5.0,
        "license_number": "FL-PSC-2024",
    },
}

AGENTS = [
    {"id": "brendan", "name": "Brendan O'Neal", "license": "W123456"},
    {"id": "jason", "name": "Jason Taylor", "license": "W789012"},
]

AGENT_ALIAS = {
    "Brendan": "Brendan O'Neal", "brendan": "Brendan O'Neal",
    "Jason": "Jason Taylor", "jason": "Jason Taylor",
}


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_month(s: str | None):
    """Parse YYYY-MM to (start, end) datetimes."""
    if not s:
        now = _utc_now()
        s = now.strftime("%Y-%m")
    try:
        start = datetime.strptime(s + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end, s
    except ValueError:
        now = _utc_now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end, now.strftime("%Y-%m")


def _normalize_surety(raw: str | None) -> str:
    s = (raw or "OSI").upper()
    if "PALMETTO" in s or "PSC" in s:
        return "PALMETTO"
    return "OSI"


def _calc_split(bond_amount: float, surety: str) -> dict:
    rates = SURETY_RATES.get(surety, SURETY_RATES["OSI"])
    premium = bond_amount * 0.10
    surety_owed = premium * (rates["surety_pct"] / 100.0)
    buf_owed = premium * (rates["buf_pct"] / 100.0)
    agent_retains = premium - surety_owed - buf_owed
    return {
        "premium": round(premium, 2),
        "surety_owed": round(surety_owed, 2),
        "buf_owed": round(buf_owed, 2),
        "agent_retains": round(agent_retains, 2),
    }


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# MONTHLY SUMMARY — Core FLDFS filing data
# ─────────────────────────────────────────────────────────────────────────────
@fldfs_bp.route("/compliance/monthly-summary")
async def monthly_summary():
    """Monthly premium volume, liability, BUF obligations by surety."""
    try:
        db = get_db()
        month_str = request.args.get("month")
        surety_filter = request.args.get("surety", "").strip().upper()
        start, end, period = _parse_month(month_str)

        col = db["active_bonds"]
        query: dict[str, Any] = {}
        if surety_filter:
            query["$or"] = [
                {"surety": surety_filter},
                {"insurance_company": {"$regex": surety_filter, "$options": "i"}},
            ]

        # Bonds written this month
        written_query = {**query, "bond_date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
        written = await col.find(written_query, {"_id": 0}).to_list(2000)

        # All active bonds (total liability)
        active_query = {**query, "status": {"$in": ["active", "monitoring", "alert"]}}
        active = await col.find(active_query, {"_id": 0}).to_list(5000)

        # Discharged this month
        discharged_query = {
            **query,
            "status": {"$in": ["exonerated", "surrendered"]},
        }
        discharged = await col.find(discharged_query, {"_id": 0}).to_list(2000)
        discharged_this_month = [
            d for d in discharged
            if (d.get("updated_at") or d.get("bond_date", "")) >= start.isoformat()
            and (d.get("updated_at") or d.get("bond_date", "")) < end.isoformat()
        ]

        # Forfeitures this month
        forfeited_query = {**query, "status": "forfeited"}
        forfeited = await col.find(forfeited_query, {"_id": 0}).to_list(1000)
        forfeited_this_month = [
            d for d in forfeited
            if (d.get("updated_at") or d.get("bond_date", "")) >= start.isoformat()
            and (d.get("updated_at") or d.get("bond_date", "")) < end.isoformat()
        ]

        # Calculate per-surety breakdown
        surety_breakdown = {}
        for bond in written:
            s = _normalize_surety(bond.get("surety") or bond.get("insurance_company"))
            ba = float(bond.get("bond_amount", 0) or 0)
            split = _calc_split(ba, s)
            if s not in surety_breakdown:
                surety_breakdown[s] = {
                    "surety": s,
                    "surety_info": SURETY_RATES.get(s, {}),
                    "bonds_written": 0,
                    "total_bond_amount": 0.0,
                    "total_premium": 0.0,
                    "total_surety_owed": 0.0,
                    "total_buf_owed": 0.0,
                    "total_agent_retains": 0.0,
                }
            g = surety_breakdown[s]
            g["bonds_written"] += 1
            g["total_bond_amount"] += ba
            g["total_premium"] += split["premium"]
            g["total_surety_owed"] += split["surety_owed"]
            g["total_buf_owed"] += split["buf_owed"]
            g["total_agent_retains"] += split["agent_retains"]

        for g in surety_breakdown.values():
            for k in ("total_bond_amount", "total_premium", "total_surety_owed",
                       "total_buf_owed", "total_agent_retains"):
                g[k] = round(g[k], 2)

        # Active liability
        total_active_liability = sum(float(b.get("bond_amount", 0) or 0) for b in active)
        total_forfeiture_exposure = sum(float(b.get("bond_amount", 0) or 0) for b in forfeited_this_month)

        return jsonify({
            "success": True,
            "report_type": "FLDFS Monthly Summary",
            "period": period,
            "generated_at": _utc_now().isoformat(),
            "agency": {
                "name": "Shamrock Bail Bonds",
                "license": "BBA0001234",
                "address": "1528 Broadway, Fort Myers, FL 33901",
                "phone": "(239) 237-1122",
            },
            "surety_breakdown": list(surety_breakdown.values()),
            "totals": {
                "bonds_written": len(written),
                "total_premium": round(sum(g["total_premium"] for g in surety_breakdown.values()), 2),
                "total_surety_owed": round(sum(g["total_surety_owed"] for g in surety_breakdown.values()), 2),
                "total_buf_owed": round(sum(g["total_buf_owed"] for g in surety_breakdown.values()), 2),
                "total_agent_retains": round(sum(g["total_agent_retains"] for g in surety_breakdown.values()), 2),
                "active_bond_count": len(active),
                "active_liability": round(total_active_liability, 2),
                "discharged_this_month": len(discharged_this_month),
                "forfeited_this_month": len(forfeited_this_month),
                "forfeiture_exposure": round(total_forfeiture_exposure, 2),
            },
        })
    except Exception as exc:
        logger.exception("compliance/monthly-summary error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 1099 — Commission breakdown per writing agent
# ─────────────────────────────────────────────────────────────────────────────
@fldfs_bp.route("/compliance/agent-1099")
async def agent_1099():
    """Per-agent commission data for 1099 reporting."""
    try:
        db = get_db()
        month_str = request.args.get("month")
        start, end, period = _parse_month(month_str)
        yearly = request.args.get("yearly", "").lower() == "true"

        col = db["active_bonds"]
        if yearly:
            year_start = start.replace(month=1, day=1)
            query = {"bond_date": {"$gte": year_start.isoformat(), "$lt": end.isoformat()}}
            period = f"{year_start.strftime('%Y')}-YTD"
        else:
            query = {"bond_date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}

        docs = await col.find(query, {"_id": 0}).to_list(5000)

        agent_map: dict[str, dict] = {}
        for d in docs:
            name = d.get("agent_name") or "Unassigned"
            name = AGENT_ALIAS.get(name, name)
            ba = float(d.get("bond_amount", 0) or 0)
            s = _normalize_surety(d.get("surety") or d.get("insurance_company"))
            split = _calc_split(ba, s)

            if name not in agent_map:
                agent_info = next((a for a in AGENTS if a["name"] == name), {})
                agent_map[name] = {
                    "agent_name": name,
                    "license": agent_info.get("license", ""),
                    "bond_count": 0,
                    "total_bond_amount": 0.0,
                    "total_premium": 0.0,
                    "total_commission": 0.0,
                    "by_surety": {},
                }
            a = agent_map[name]
            a["bond_count"] += 1
            a["total_bond_amount"] += ba
            a["total_premium"] += split["premium"]
            a["total_commission"] += split["agent_retains"]
            a["by_surety"][s] = a["by_surety"].get(s, 0) + 1

        agents = []
        for a in sorted(agent_map.values(), key=lambda x: x["total_commission"], reverse=True):
            a["total_bond_amount"] = round(a["total_bond_amount"], 2)
            a["total_premium"] = round(a["total_premium"], 2)
            a["total_commission"] = round(a["total_commission"], 2)
            agents.append(a)

        return jsonify({
            "success": True,
            "report_type": "Agent 1099 Commission Report",
            "period": period,
            "generated_at": _utc_now().isoformat(),
            "agents": agents,
            "grand_total_commission": round(sum(a["total_commission"] for a in agents), 2),
        })
    except Exception as exc:
        logger.exception("compliance/agent-1099 error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POA UTILIZATION — Usage rates, velocity, depletion forecast
# ─────────────────────────────────────────────────────────────────────────────
@fldfs_bp.route("/compliance/poa-utilization")
async def poa_utilization():
    """POA inventory utilization rates and depletion forecasting."""
    try:
        db = get_db()
        month_str = request.args.get("month")
        start, end, period = _parse_month(month_str)

        poa_col = db["poa_inventory"]
        bonds_col = db["active_bonds"]

        # Current inventory by surety/prefix
        pipe = [
            {"$group": {
                "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix", "status": "$status"},
                "count": {"$sum": 1},
                "max_bond_value": {"$max": "$max_bond_value"},
            }},
            {"$sort": {"_id.surety_id": 1, "_id.poa_prefix": 1}},
        ]
        raw = await poa_col.aggregate(pipe).to_list(None)

        tiers: dict[str, dict] = {}
        for r in raw:
            key = f"{r['_id']['surety_id']}:{r['_id']['poa_prefix']}"
            if key not in tiers:
                tiers[key] = {
                    "surety_id": r["_id"]["surety_id"],
                    "poa_prefix": r["_id"]["poa_prefix"],
                    "max_bond_value": r.get("max_bond_value", 0) or 0,
                    "available": 0, "assigned": 0, "voided": 0, "total": 0,
                }
            tiers[key][r["_id"]["status"]] = r["count"]
            tiers[key]["total"] += r["count"]

        # Bonds written this month (to calculate velocity)
        month_bonds = await bonds_col.count_documents(
            {"bond_date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
        )
        days_elapsed = max((_utc_now() - start).days, 1)
        daily_velocity = round(month_bonds / days_elapsed, 2)

        tier_list = []
        for t in sorted(tiers.values(), key=lambda x: (x["surety_id"], x["max_bond_value"])):
            avail = t.get("available", 0)
            days_until_depleted = round(avail / daily_velocity, 1) if daily_velocity > 0 else 999
            utilization_pct = round((t.get("assigned", 0) / max(t["total"], 1)) * 100, 1)
            tier_list.append({
                **t,
                "utilization_pct": utilization_pct,
                "days_until_depleted": days_until_depleted,
                "depletion_risk": "critical" if days_until_depleted < 7 else
                                  "warning" if days_until_depleted < 30 else "ok",
            })

        return jsonify({
            "success": True,
            "report_type": "POA Utilization & Depletion Forecast",
            "period": period,
            "generated_at": _utc_now().isoformat(),
            "daily_velocity": daily_velocity,
            "month_bonds_written": month_bonds,
            "tiers": tier_list,
        })
    except Exception as exc:
        logger.exception("compliance/poa-utilization error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# FORFEITURE LOG — Estreature compliance tracking
# ─────────────────────────────────────────────────────────────────────────────
@fldfs_bp.route("/compliance/forfeiture-log")
async def forfeiture_log():
    """Forfeiture/estreature compliance log with timelines."""
    try:
        db = get_db()
        month_str = request.args.get("month")
        start, end, period = _parse_month(month_str)

        col = db["active_bonds"]
        query = {"status": {"$in": ["forfeited", "surrendered"]}}
        docs = await col.find(query, {"_id": 0}).sort("bond_date", -1).to_list(1000)

        entries = []
        for d in docs:
            _serialize(d)
            ba = float(d.get("bond_amount", 0) or 0)
            s = _normalize_surety(d.get("surety") or d.get("insurance_company"))

            # Calculate days since bond date
            bond_date_str = d.get("bond_date", "")
            days_outstanding = 0
            if bond_date_str:
                try:
                    bd = datetime.fromisoformat(bond_date_str.replace("Z", "+00:00"))
                    days_outstanding = (_utc_now() - bd).days
                except (ValueError, TypeError):
                    pass

            entries.append({
                "defendant_name": d.get("defendant_name", ""),
                "booking_number": d.get("booking_number", ""),
                "county": d.get("county", ""),
                "case_number": d.get("case_number", ""),
                "bond_amount": ba,
                "bond_date": bond_date_str,
                "status": d.get("status", ""),
                "surety": s,
                "poa_number": d.get("poa_number", ""),
                "agent_name": d.get("agent_name", ""),
                "days_outstanding": days_outstanding,
                "estreature_deadline": d.get("estreature_deadline", ""),
                "court_date": d.get("court_date", ""),
            })

        total_exposure = sum(e["bond_amount"] for e in entries)

        return jsonify({
            "success": True,
            "report_type": "Forfeiture & Estreature Compliance Log",
            "period": period,
            "generated_at": _utc_now().isoformat(),
            "entries": entries,
            "count": len(entries),
            "total_exposure": round(total_exposure, 2),
            "avg_days_outstanding": round(
                sum(e["days_outstanding"] for e in entries) / max(len(entries), 1), 1
            ),
        })
    except Exception as exc:
        logger.exception("compliance/forfeiture-log error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# FULL FILING — Combined package for DOI submission
# ─────────────────────────────────────────────────────────────────────────────
@fldfs_bp.route("/compliance/full-filing")
async def full_filing():
    """Combined compliance package — all reports in one response."""
    try:
        from quart import current_app
        async with current_app.test_request_context(
            f"/api/compliance/monthly-summary?month={request.args.get('month', '')}",
        ):
            summary_resp = await monthly_summary()
            summary = summary_resp.get_json() if hasattr(summary_resp, "get_json") else {}

        async with current_app.test_request_context(
            f"/api/compliance/agent-1099?month={request.args.get('month', '')}",
        ):
            agent_resp = await agent_1099()
            agent_data = agent_resp.get_json() if hasattr(agent_resp, "get_json") else {}

        async with current_app.test_request_context(
            f"/api/compliance/poa-utilization?month={request.args.get('month', '')}",
        ):
            poa_resp = await poa_utilization()
            poa_data = poa_resp.get_json() if hasattr(poa_resp, "get_json") else {}

        async with current_app.test_request_context(
            f"/api/compliance/forfeiture-log?month={request.args.get('month', '')}",
        ):
            forf_resp = await forfeiture_log()
            forf_data = forf_resp.get_json() if hasattr(forf_resp, "get_json") else {}

        return jsonify({
            "success": True,
            "report_type": "FLDFS Full Compliance Filing Package",
            "generated_at": _utc_now().isoformat(),
            "monthly_summary": summary,
            "agent_commissions": agent_data,
            "poa_utilization": poa_data,
            "forfeiture_log": forf_data,
        })
    except Exception as exc:
        logger.exception("compliance/full-filing error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
