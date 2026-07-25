from __future__ import annotations

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

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from dashboard.extensions import get_db
from dashboard.services.bond_report_xlsx import (
    REPORT_ROW_LIMIT,
    bond_data_quality,
    build_official_bond_report,
    filename_for,
    mongo_bond_date_filter,
    normalize_bond_date_str,
    parse_report_date_window,
)

# Statuses that still represent open surety liability (open book)
_OPEN_LIABILITY_STATUSES = {
    "active", "monitoring", "alert", "reinstated", "posted", "open", "",
}
_CLOSED_STATUSES = {
    "void", "voided", "expired", "exonerated", "surrendered",
    "discharged", "forfeited", "closed", "cancelled", "VOID",
}

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
    """Parse YYYY-MM-DD string to UTC datetime (legacy helper; clamps via window parser preferred)."""
    start, _, _ = parse_report_date_window(s, None)
    if start is None:
        return None
    return start.replace(tzinfo=timezone.utc)


def _date_filter(
    field: str = "bond_date",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Build a MongoDB date range filter from optional date strings.

    Uses YYYY-MM-DD string bounds (matching POA execute storage) and clamps
    to the 2012 report epoch. Invalid / pre-2012 inputs never raise.
    """
    filt, _warnings = mongo_bond_date_filter(start_date, end_date, field=field)
    return filt


def _date_filter_with_warnings(
    field: str = "bond_date",
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[dict, list[str], str | None, str | None]:
    """Date filter plus diagnostics for API responses (2012 clamp, swaps, bad dates)."""
    start_dt, end_dt, warnings = parse_report_date_window(start_date, end_date)
    filt, _ = mongo_bond_date_filter(
        start_dt.strftime("%Y-%m-%d") if start_dt else None,
        end_dt.strftime("%Y-%m-%d") if end_dt else None,
        field=field,
    )
    return (
        filt,
        warnings,
        start_dt.strftime("%Y-%m-%d") if start_dt else None,
        end_dt.strftime("%Y-%m-%d") if end_dt else None,
    )


def _status_scope_filter(scope: str | None) -> dict:
    """``open`` = outstanding liability; ``all`` = non-void; ``closed`` = discharged set."""
    s = (scope or "open").strip().lower()
    if s in ("all", "any", "full"):
        return {"status": {"$nin": ["void", "voided", "VOID", "expired"]}}
    if s in ("closed", "discharged"):
        return {"status": {"$in": ["exonerated", "surrendered", "discharged", "forfeited", "closed"]}}
    # Default: open book liability for surety statements
    return {"status": {"$nin": list(_CLOSED_STATUSES)}}


def _prior_period_bounds(
    start_iso: str | None, end_iso: str | None
) -> tuple[str | None, str | None]:
    """SuiteCRM / Salesforce-style equal-length prior window immediately before start."""
    if not start_iso or not end_iso:
        return None, None
    try:
        start = datetime.strptime(start_iso[:10], "%Y-%m-%d")
        end = datetime.strptime(end_iso[:10], "%Y-%m-%d")
    except ValueError:
        return None, None
    if end < start:
        return None, None
    days = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    # Never go before report epoch
    epoch = datetime(2012, 1, 1)
    if prior_start < epoch:
        prior_start = epoch
    if prior_end < epoch:
        return None, None
    return prior_start.strftime("%Y-%m-%d"), prior_end.strftime("%Y-%m-%d")


def _pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None if current == 0 else 100.0
    return round(((current - prior) / abs(prior)) * 100.0, 1)


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
async def discharged_bonds(
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
):
    """Bonds with status 'exonerated' or 'surrendered'."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {"status": {"$in": ["exonerated", "surrendered"]}}
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "bond_date", start_date, end_date
        )
        query.update(date_filt)

        # Oldest bond written first (2012+ windows supported; no current-year cap)
        docs = await col.find(query, {"_id": 0}).sort("bond_date", 1).to_list(REPORT_ROW_LIMIT)
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
            "sort_order": "bond_date ascending (oldest bond first)",
            "start_date": resolved_start,
            "end_date": resolved_end,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/discharged error: %s", exc)
        return JSONResponse(
            {
                "success": False,
                "error": str(exc)[:400],
                "error_type": type(exc).__name__,
                "hint": "Date range accepts 2012-01-01 → today; bond_date should be YYYY-MM-DD.",
            },
            status_code=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SURETY LIABILITY STATEMENT
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/surety-liability")
async def surety_liability(
    surety: str = Query(default=""),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    county: str = Query(default=None),
    status_scope: str = Query(default="open", description="open | all | closed"),
):
    """Per-surety financial breakdown: bond amounts, premium, surety owed, BUF, agent retains.

    Default ``status_scope=open`` = outstanding open-book liability (excludes
    exonerated/forfeited/void). Use ``all`` for historical production audit.
    Rows are always oldest bond written → newest.
    """
    try:
        db = get_db()
        col = db["active_bonds"]
        surety_filter = surety.strip().upper()

        query: dict = {}
        query.update(_status_scope_filter(status_scope))
        if surety_filter:
            query["$or"] = [
                {"surety": surety_filter},
                {"surety": {"$regex": surety_filter, "$options": "i"}},
                {"insurance_company": {"$regex": surety_filter, "$options": "i"}},
            ]
        if county and county.strip():
            query["county"] = {"$regex": f"^{county.strip()}$", "$options": "i"}
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "bond_date", start_date, end_date
        )
        query.update(date_filt)

        # Oldest → newest; supports full 2012 → present windows (no year cap)
        docs = await col.find(query, {"_id": 0}).sort("bond_date", 1).to_list(REPORT_ROW_LIMIT)
        truncated = len(docs) >= REPORT_ROW_LIMIT
        if truncated:
            date_warnings.append(
                f"query hit row limit ({REPORT_ROW_LIMIT}); narrow the date range if rows look missing"
            )

        quality = bond_data_quality(docs)
        if quality["undated_count"]:
            date_warnings.append(
                f"{quality['undated_count']} bond(s) missing parseable dates — listed after dated rows"
            )
        if quality["missing_power_count"]:
            date_warnings.append(
                f"{quality['missing_power_count']} bond(s) missing power/POA number"
            )

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
            bond_date_raw = (
                d.get("bond_date") or d.get("date_executed") or d.get("posted_date") or d.get("created_at") or ""
            )
            bond_date_norm = normalize_bond_date_str(bond_date_raw) or str(bond_date_raw or "")[:10]

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
                "poa_number": d.get("poa_number") or d.get("poa_full") or "",
                "poa_prefix": d.get("poa_prefix") or "",
                "defendant_name": d.get("defendant_name", "") or f"{d.get('defendant_first_name', '')} {d.get('defendant_last_name', '')}".strip(),
                "defendant_first_name": d.get("defendant_first_name") or "",
                "defendant_last_name": d.get("defendant_last_name") or "",
                "booking_number": d.get("booking_number", ""),
                "county": d.get("county", ""),
                "bond_amount": ba,
                "bond_date": bond_date_norm,
                "status": d.get("status", ""),
                "charge": d.get("charge") or "",
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

        # Prior-period comparison (equal-length window immediately before range)
        comparison = None
        p_start, p_end = _prior_period_bounds(resolved_start, resolved_end)
        if p_start and p_end:
            prior_q = dict(query)
            # replace bond_date window
            prior_filt, _ = mongo_bond_date_filter(p_start, p_end, field="bond_date")
            prior_q.pop("bond_date", None)
            prior_q.update(prior_filt)
            prior_docs = await col.find(prior_q, {"_id": 0, "bond_amount": 1, "premium": 1}).to_list(
                REPORT_ROW_LIMIT
            )
            prior_liab = round(sum(float(d.get("bond_amount") or 0) for d in prior_docs), 2)
            prior_bonds = len(prior_docs)
            prior_prem = round(sum(float(d.get("premium") or 0) for d in prior_docs), 2)
            comparison = {
                "prior_start": p_start,
                "prior_end": p_end,
                "prior_bonds": prior_bonds,
                "prior_bond_amount": prior_liab,
                "prior_premium": prior_prem,
                "bonds_pct_change": _pct_change(float(grand["total_bonds"]), float(prior_bonds)),
                "liability_pct_change": _pct_change(grand["total_bond_amount"], prior_liab),
                "premium_pct_change": _pct_change(grand["total_premium"], prior_prem),
            }

        return {
            "success": True,
            "sureties": list(surety_groups.values()),
            "grand_totals": grand,
            "comparison": comparison,
            "sort_order": "bond_date ascending (oldest bond first)",
            "status_scope": (status_scope or "open").strip().lower(),
            "start_date": resolved_start,
            "end_date": resolved_end,
            "county": county.strip() if county else None,
            "truncated": truncated,
            "data_quality": quality,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/surety-liability error: %s", exc)
        return JSONResponse(
            {
                "success": False,
                "error": str(exc)[:400],
                "error_type": type(exc).__name__,
                "hint": "Date range accepts 2012-01-01 → today; bond_date should be YYYY-MM-DD.",
            },
            status_code=500,
        )


@reports_bp.get("/reports/bond-report.xlsx")
async def official_bond_report_xlsx(
    surety: str = Query(default="OSI"),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    county: str = Query(default=None),
    status_scope: str = Query(default="open"),
    include_discharges: bool = Query(default=True),
):
    """Download official multi-sheet surety XLSX (oldest → newest), ready for submission."""
    try:
        db = get_db()
        col = db["active_bonds"]
        surety_key = (surety or "OSI").strip().upper()
        if surety_key not in ("OSI", "PALMETTO"):
            if "PALM" in surety_key or surety_key == "PSC":
                surety_key = "PALMETTO"
            else:
                surety_key = "OSI"

        query: dict = {}
        query.update(_status_scope_filter(status_scope))
        query["$or"] = [
            {"surety": {"$regex": surety_key, "$options": "i"}},
            {"surety_id": {"$regex": surety_key, "$options": "i"}},
            {"insurance_company": {"$regex": surety_key, "$options": "i"}},
        ]
        if county and county.strip():
            query["county"] = {"$regex": f"^{county.strip()}$", "$options": "i"}
        date_filt, _warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "bond_date", start_date, end_date
        )
        query.update(date_filt)

        docs = await col.find(query, {"_id": 0}).sort("bond_date", 1).to_list(REPORT_ROW_LIMIT)
        voids = await col.find(
            {
                "status": {"$in": ["void", "voided", "expired", "VOID"]},
                "$or": query["$or"],
            },
            {"_id": 0},
        ).sort("bond_date", 1).to_list(500)
        discharges = []
        if include_discharges:
            dis_q: dict = {
                "status": {"$in": ["exonerated", "surrendered", "discharged"]},
                "$or": query["$or"],
            }
            if date_filt:
                dis_q.update(date_filt)
            discharges = await col.find(dis_q, {"_id": 0}).sort("bond_date", 1).to_list(2000)

        xlsx = build_official_bond_report(
            docs,
            surety=surety_key,
            report_type="Surety Bond Liability Report",
            voids=voids,
            discharges=discharges,
            period_start=resolved_start,
            period_end=resolved_end,
        )
        fname = filename_for(surety_key, "Bond_Report")

        # Archive for "recent reports" drawer (SuiteCRM-style report history)
        try:
            import base64
            meta = {
                "ok": True,
                "report_type": "bond_report",
                "source": "dashboard_xlsx",
                "surety": surety_key,
                "filename": fname,
                "size_bytes": len(xlsx),
                "active_rows": len(docs),
                "voids": len(voids),
                "discharges": len(discharges),
                "start_date": resolved_start,
                "end_date": resolved_end,
                "status_scope": (status_scope or "open").strip().lower(),
                "sort_order": "bond_date ascending (oldest bond first)",
                "created_at": _utc_now(),
                "xlsx_b64": base64.b64encode(xlsx).decode("ascii")
                if len(xlsx) < 12_000_000
                else None,
            }
            await db["generated_reports"].insert_one(meta)
        except Exception as store_err:
            logger.warning("bond-report.xlsx archive failed: %s", store_err)

        return Response(
            content=xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Sort-Order": "bond_date-ascending",
                "X-Row-Count": str(len(docs)),
            },
        )
    except Exception as exc:
        logger.exception("reports/bond-report.xlsx error: %s", exc)
        return JSONResponse(
            {
                "success": False,
                "error": str(exc)[:400],
                "error_type": type(exc).__name__,
                "hint": "Ensure openpyxl is installed and active_bonds is reachable.",
            },
            status_code=500,
        )


@reports_bp.get("/reports/generated")
async def list_generated_reports(limit: int = Query(default=15, ge=1, le=50)):
    """Recent official reports archived in Mongo (no XLSX payload — metadata only)."""
    try:
        db = get_db()
        col = db["generated_reports"]
        docs = await col.find(
            {},
            {"xlsx_b64": 0, "xlsx_base64": 0},
        ).sort("created_at", -1).to_list(limit)
        out = []
        for d in docs:
            rid = str(d.pop("_id", ""))
            created = d.get("created_at")
            if isinstance(created, datetime):
                d["created_at"] = created.isoformat()
            d["id"] = rid
            d["has_file"] = True  # may still be missing if oversized at store time
            out.append(d)
        return {"success": True, "reports": out, "count": len(out)}
    except Exception as exc:
        logger.exception("reports/generated error: %s", exc)
        return JSONResponse(
            {"success": False, "error": str(exc)[:400], "error_type": type(exc).__name__},
            status_code=500,
        )


@reports_bp.get("/reports/generated/{report_id}/download")
async def download_generated_report(report_id: str):
    """Re-download an archived XLSX by id (PII-bearing — dashboard auth required)."""
    try:
        from bson import ObjectId
        import base64

        db = get_db()
        try:
            oid = ObjectId(report_id)
        except Exception:
            return JSONResponse({"success": False, "error": "Invalid report id"}, status_code=400)
        doc = await db["generated_reports"].find_one({"_id": oid})
        if not doc:
            return JSONResponse({"success": False, "error": "Report not found"}, status_code=404)
        b64 = doc.get("xlsx_b64") or doc.get("xlsx_base64")
        if not b64:
            return JSONResponse(
                {
                    "success": False,
                    "error": "File not stored (report exceeded archive size limit)",
                    "hint": "Re-generate via Reports → XLSX export.",
                },
                status_code=404,
            )
        raw = base64.b64decode(b64)
        fname = doc.get("filename") or "Shamrock_Bond_Report.xlsx"
        return Response(
            content=raw,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    except Exception as exc:
        logger.exception("reports/generated download error: %s", exc)
        return JSONResponse(
            {"success": False, "error": str(exc)[:400], "error_type": type(exc).__name__},
            status_code=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# VOIDED POWERS
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/voided-powers")
async def voided_powers(
    surety: str = Query(default=""),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
):
    """POAs that were manually voided."""
    try:
        db = get_db()
        col = db["poa_inventory"]
        query = {"status": "voided"}
        surety_filter = surety.strip().lower()
        if surety_filter:
            query["surety_id"] = surety_filter
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "voided_at", start_date, end_date
        )
        query.update(date_filt)

        docs = await col.find(query, {"_id": 0}).sort("voided_at", -1).to_list(REPORT_ROW_LIMIT)
        for d in docs:
            _serialize_doc(d)

        return {
            "success": True,
            "powers": docs,
            "records": docs,
            "count": len(docs),
            "start_date": resolved_start,
            "end_date": resolved_end,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/voided-powers error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED POWERS (semi-annual expiration)
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/expired-powers")
async def expired_powers(
    surety: str = Query(default=""),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
):
    """POAs past their expiration date (semi-annual cycle)."""
    try:
        db = get_db()
        col = db["poa_inventory"]
        now_iso = _utc_now().isoformat()

        query = {
            "expiration": {"$ne": None, "$lt": now_iso},
            "status": {"$nin": ["voided"]},  # Don't double-count voided ones
        }
        surety_filter = surety.strip().lower()
        if surety_filter:
            query["surety_id"] = surety_filter
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "expiration", start_date, end_date
        )
        query.update(date_filt)

        docs = await col.find(query, {"_id": 0}).sort("expiration", 1).to_list(REPORT_ROW_LIMIT)
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
            "start_date": resolved_start,
            "end_date": resolved_end,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/expired-powers error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# FORFEITURES
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/forfeitures")
async def forfeitures(
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
):
    """Bonds with status 'forfeited'."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {"status": "forfeited"}
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "bond_date", start_date, end_date
        )
        query.update(date_filt)

        docs = await col.find(query, {"_id": 0}).sort("bond_date", 1).to_list(REPORT_ROW_LIMIT)
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
            "sort_order": "bond_date ascending (oldest bond first)",
            "start_date": resolved_start,
            "end_date": resolved_end,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/forfeitures error: %s", exc)
        return JSONResponse(
            {
                "success": False,
                "error": str(exc)[:400],
                "error_type": type(exc).__name__,
                "hint": "Date range accepts 2012-01-01 → today; bond_date should be YYYY-MM-DD.",
            },
            status_code=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT PRODUCTION
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.get("/reports/agent-production")
async def agent_production(
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    county: str = Query(default=None),
    surety: str = Query(default=None),
):
    """Per-agent bond count, premium, avg bond, surety breakdown, production metrics."""
    try:
        db = get_db()
        col = db["active_bonds"]
        query = {}
        date_filt, date_warnings, resolved_start, resolved_end = _date_filter_with_warnings(
            "bond_date", start_date, end_date
        )
        query.update(date_filt)
        if county and county.strip():
            query["county"] = {"$regex": f"^{county.strip()}$", "$options": "i"}
        if surety and surety.strip():
            sf = surety.strip()
            query["$or"] = [
                {"surety": {"$regex": sf, "$options": "i"}},
                {"insurance_company": {"$regex": sf, "$options": "i"}},
            ]

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
                    "surety": {
                        "$switch": {
                            "branches": [
                                {
                                    "case": {"$regexMatch": {
                                        "input": {"$ifNull": ["$insurance_company", ""]},
                                        "regex": "palmetto|psc",
                                        "options": "i"
                                    }},
                                    "then": "PALMETTO"
                                },
                            ],
                            "default": "OSI"
                        }
                    },
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
            "start_date": resolved_start,
            "end_date": resolved_end,
            "warnings": date_warnings or None,
        }
    except Exception as exc:
        logger.exception("reports/agent-production error: %s", exc)
        return JSONResponse(
            {
                "success": False,
                "error": str(exc)[:400],
                "error_type": type(exc).__name__,
                "hint": "Date range accepts 2012-01-01 → today; bond_date should be YYYY-MM-DD.",
            },
            status_code=500,
        )


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
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


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
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@reports_bp.get("/reports/kpi-trends")
async def kpi_trends(days: int = Query(default=30)):
    """Return period-over-period KPI comparison for the Reports tab trend indicators."""
    try:
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        period_days = int(days)
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
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

