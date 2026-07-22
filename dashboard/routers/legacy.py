from __future__ import annotations

"""
ShamrockLeads — Legacy API Blueprint
Migrated from app.py Flask endpoints to Quart async handlers.

Endpoints:
  /api/health-full       — Full MongoDB health check
  /api/cleanup           — Trigger manual data cleanup
  /api/db-health         — MongoDB Atlas M0 storage health
  /api/leads/update-custody  — Manual custody status override
  /api/imessage/status   — BlueBubbles server status
  /api/imessage/send     — Send iMessage via BlueBubbles
  /api/imessage/history/<booking_number> — iMessage outreach history
  /api/imessage/templates — Outreach templates
  /api/config/bluebubbles-url — Dynamic URL sync from iMac
"""

import logging
import os
import re as re_mod
import secrets
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import (
    get_collection, get_db, BB_SERVERS,
    get_bb_server, format_phone, init_bluebubbles,
    update_bb_url, BB_CONFIG_API_KEY,
)

legacy_bp = APIRouter(prefix="/api", tags=["legacy"])
# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles Dynamic URL Sync (iMac → VPS, no restart needed)
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.post("/config/bluebubbles-url")
async def update_bluebubbles_url(request: Request):
    """Accept a new ngrok tunnel URL from the iMac sync script."""
    auth = request.headers.get("X-API-Key", "")
    if auth != BB_CONFIG_API_KEY:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    suffix = body.get("suffix", "0178")
    url = body.get("url", "").strip()

    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)

    servers = update_bb_url(suffix, url)
    phone_key = f"239955{suffix}"
    active = servers.get(phone_key, {})

    return {
        "success": True,
        "suffix": suffix,
        "url": url,
        "active_servers": {k: {"url": v["url"], "label": v["label"]} for k, v in servers.items()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Health / System
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.get("/health-full")
async def api_health_full():
    """Full system health — checks MongoDB connectivity."""
    arrests = get_collection("arrests")
    mongo_ok = False
    try:
        db = get_db()
        await db.command("ping")
        mongo_ok = True
    except Exception as _ping_err:
        logger.warning("[legacy] MongoDB ping failed: %s", _ping_err)

    total_arrests = 0
    active_counties = 0
    try:
        total_arrests = await arrests.estimated_document_count()
        active_counties = len(await arrests.distinct("county"))
    except Exception as _count_err:
        logger.warning("[legacy] Stats count failed: %s", _count_err)

    status = "ok" if mongo_ok else "degraded"
    code = 200 if mongo_ok else 503
    return {
        "status": status,
        "mongodb": "connected" if mongo_ok else "disconnected",
        "total_arrests": total_arrests,
        "active_counties": active_counties,
        "uptime_check": datetime.now(timezone.utc).isoformat(),
    }, code


@legacy_bp.post("/cleanup")
async def api_cleanup():
    """Trigger manual data cleanup. Returns purge statistics."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from maintenance.cleanup import run_cleanup
        result = run_cleanup()
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@legacy_bp.get("/db-health")
async def api_db_health():
    """MongoDB Atlas storage health — monitors against 512MB M0 limit."""
    try:
        db = get_db()
        db_stats = await db.command("dbStats")
        data_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_size_mb = round(db_stats.get("storageSize", 0) / (1024 * 1024), 2)
        index_size_mb = round(db_stats.get("indexSize", 0) / (1024 * 1024), 2)

        M0_LIMIT_MB = 512
        usage_pct = round(storage_size_mb / M0_LIMIT_MB * 100, 1)

        # Per-collection breakdown
        collections_info = []
        for coll_name in ["arrests", "leads", "ingestion_log"]:
            try:
                coll_stats = await db.command("collStats", coll_name)
                collections_info.append({
                    "name": coll_name,
                    "documents": coll_stats.get("count", 0),
                    "data_size_mb": round(coll_stats.get("size", 0) / (1024 * 1024), 2),
                    "storage_size_mb": round(coll_stats.get("storageSize", 0) / (1024 * 1024), 2),
                    "index_size_mb": round(coll_stats.get("totalIndexSize", 0) / (1024 * 1024), 2),
                })
            except Exception:
                collections_info.append({"name": coll_name, "error": "not found"})

        status = "healthy"
        if usage_pct > 85:
            status = "critical"
        elif usage_pct > 70:
            status = "warning"

        return {
            "status": status,
            "limit_mb": M0_LIMIT_MB,
            "data_size_mb": data_size_mb,
            "storage_size_mb": storage_size_mb,
            "index_size_mb": index_size_mb,
            "usage_pct": usage_pct,
            "collections": collections_info,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  Manual Custody Status Override
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.post("/leads/update-custody")
async def update_custody(request: Request):
    """Manually override custody status for a defendant."""
    arrests = get_collection("arrests")
    body = await request.json()
    booking_number = body.get("booking_number", "").strip()
    new_status = body.get("custody_status", "").strip()

    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)

    valid_statuses = ["In Custody", "Not In Custody", "Released", "Bonded Out"]
    if new_status not in valid_statuses:
        return JSONResponse({"error": f"Invalid status. Must be one of: {valid_statuses}"}, status_code=400)

    try:
        existing = await arrests.find_one(
            {"booking_number": booking_number},
            {"status": 1, "custody_overrides": 1}
        )
        if not existing:
            return JSONResponse({"error": f"No record found for booking {booking_number}"}, status_code=404)

        old_status = existing.get("status", "Unknown")

        override_entry = {
            "old_status": old_status,
            "new_status": new_status,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "changed_by": body.get("changed_by", "dashboard_user"),
        }

        result = await arrests.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": new_status,
                    "custody_override": True,
                    "custody_override_at": datetime.now(timezone.utc).isoformat(),
                },
                "$push": {
                    "custody_overrides": override_entry,
                },
            },
        )

        if result.modified_count == 0:
            return JSONResponse({"error": "Record found but not modified"}, status_code=500)

        return {
            "success": True,
            "booking_number": booking_number,
            "old_status": old_status,
            "new_status": new_status,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  Manual Bond Amount Override
#  Scrapers often capture $0 until first appearance (1–48h later). Staff must
#  be able to set the real bond so premium/billing/Write Bond work.
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_money(raw) -> float:
    """Parse '$5,000.00' / '5000' / 5000 → float. Raises ValueError if invalid."""
    if raw is None or raw == "":
        raise ValueError("bond_amount is required")
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        raise ValueError("bond_amount is required")
    return float(s)


@legacy_bp.post("/leads/update-bond-amount")
async def update_bond_amount(request: Request):
    """Manually set / correct bond amount for an arrest lead.

    Updates ``arrests`` (source of truth for Defendants tab) and mirrors to
    ``prospective_bonds`` / ``active_bonds`` when present. Records an audit
    trail under ``bond_amount_overrides``.
    """
    body = await request.json()
    booking_number = (body.get("booking_number") or "").strip()
    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)

    try:
        new_amount = _parse_money(body.get("bond_amount"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": f"Invalid bond_amount: {exc}"}, status_code=400)

    if new_amount < 0:
        return JSONResponse({"error": "bond_amount cannot be negative"}, status_code=400)
    # Hard ceiling to catch fat-finger typos (e.g. extra zeros)
    if new_amount > 50_000_000:
        return JSONResponse({"error": "bond_amount exceeds $50M sanity limit"}, status_code=400)

    bond_type = (body.get("bond_type") or "").strip()
    note = (body.get("note") or "").strip()
    changed_by = (body.get("changed_by") or body.get("agent") or "dashboard_user").strip()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    arrests = get_collection("arrests")
    try:
        existing = await arrests.find_one(
            {"booking_number": booking_number},
            {"bond_amount": 1, "total_bond_amount": 1, "bond_type": 1, "lead_score": 1, "lead_status": 1},
        )
        if not existing:
            return JSONResponse(
                {"error": f"No arrest record found for booking {booking_number}"},
                status_code=404,
            )

        old_amount = existing.get("bond_amount")
        if old_amount is None:
            old_amount = existing.get("total_bond_amount", 0)
        try:
            old_amount_f = float(old_amount or 0)
        except (TypeError, ValueError):
            old_amount_f = 0.0

        # Bond type: if staff sets a positive bond and type was No Bond / empty, default Surety
        old_type = (existing.get("bond_type") or "").strip()
        new_type = bond_type
        if not new_type:
            if new_amount > 0 and (
                not old_type
                or old_type.upper() in ("NO BOND", "NONE", "ROR", "OR", "0", "N/A")
            ):
                new_type = "Surety"
            else:
                new_type = old_type

        override_entry = {
            "old_amount": old_amount_f,
            "new_amount": new_amount,
            "old_type": old_type,
            "new_type": new_type,
            "note": note,
            "changed_at": now_iso,
            "changed_by": changed_by,
            "source": "dashboard_manual",
        }

        set_fields = {
            "bond_amount": new_amount,
            "total_bond_amount": new_amount,
            "bond_override": True,
            "bond_override_at": now_iso,
            "bond_override_by": changed_by,
            "updated_at": now_iso,
        }
        if new_type:
            set_fields["bond_type"] = new_type

        # Lightweight lead re-score so $0 → real bond doesn't stay Disqualified/Cold forever
        lead_score = None
        lead_status = None
        try:
            # Mirror scoring/lead_scorer bond bands (approximate, intentional)
            if new_amount <= 0:
                bond_pts = -50
            elif new_amount < 500:
                bond_pts = -10
            elif new_amount <= 50_000:
                bond_pts = 30
            elif new_amount <= 100_000:
                bond_pts = 20
            else:
                bond_pts = 10
            base = int(existing.get("lead_score") or 0)
            # Replace previous bond contribution roughly by re-basing around current score
            # Prefer a clean recompute if amount was 0 (common scraper gap)
            if old_amount_f <= 0 and new_amount > 0:
                # Restore points that a $0 disqualifier would have stripped
                lead_score = max(0, min(100, base + 50 + bond_pts))
            else:
                lead_score = max(0, min(100, base + (bond_pts - (-50 if old_amount_f <= 0 else 0))))
            if lead_score >= 80:
                lead_status = "Hot"
            elif lead_score >= 50:
                lead_status = "Warm"
            elif lead_score >= 30:
                lead_status = "Cold"
            else:
                lead_status = "Disqualified"
            set_fields["lead_score"] = lead_score
            set_fields["lead_status"] = lead_status
        except Exception as score_err:
            logger.warning("[update-bond-amount] re-score skipped: %s", score_err)

        result = await arrests.update_one(
            {"booking_number": booking_number},
            {
                "$set": set_fields,
                "$push": {"bond_amount_overrides": override_entry},
            },
        )
        if result.matched_count == 0:
            return JSONResponse({"error": "Record not found"}, status_code=404)

        # Mirror to pipeline / active bonds (non-fatal)
        sync = {"prospective": 0, "active": 0}
        try:
            pb = get_collection("prospective_bonds")
            pr = await pb.update_many(
                {"booking_number": booking_number},
                {"$set": {
                    "bond_amount": new_amount,
                    "updated_at": now,
                    "bond_override": True,
                }},
            )
            sync["prospective"] = pr.modified_count
        except Exception as e:
            logger.warning("[update-bond-amount] prospective sync: %s", e)
        try:
            ab = get_collection("active_bonds")
            ar = await ab.update_many(
                {"booking_number": booking_number},
                {"$set": {
                    "bond_amount": new_amount,
                    "premium": round(new_amount * 0.10, 2),
                    "updated_at": now,
                    "bond_override": True,
                }},
            )
            sync["active"] = ar.modified_count
        except Exception as e:
            logger.warning("[update-bond-amount] active_bonds sync: %s", e)

        return {
            "success": True,
            "booking_number": booking_number,
            "old_amount": old_amount_f,
            "new_amount": new_amount,
            "bond_type": new_type,
            "lead_score": lead_score,
            "lead_status": lead_status,
            "synced": sync,
            "premium_estimate": round(new_amount * 0.10, 2),
        }
    except Exception as e:
        logger.exception("update_bond_amount failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  Refresh single defendant from originating booking-sheet URL
# ═══════════════════════════════════════════════════════════════════════════════

async def _apply_bond_amount_to_arrest(
    booking_number: str,
    new_amount: float,
    *,
    changed_by: str,
    note: str,
    bond_type: str = "",
    source: str = "dashboard_manual",
) -> dict:
    """Shared bond write used by manual edit + source refresh."""
    # Reuse update_bond_amount logic via synthetic request body
    class _Body:
        async def json(self):
            return {
                "booking_number": booking_number,
                "bond_amount": new_amount,
                "bond_type": bond_type,
                "changed_by": changed_by,
                "note": note,
                "agent": changed_by,
            }

    result = await update_bond_amount(_Body())
    if isinstance(result, JSONResponse):
        return {"success": False, "error": "bond update failed"}
    if isinstance(result, dict):
        result["source"] = source
    return result


@legacy_bp.post("/leads/refresh-from-source")
async def refresh_from_source(request: Request):
    """Re-fetch one defendant's booking sheet from the county website.

    1. Immediate: HTTP GET ``detail_url`` and parse bond amount (fast path).
    2. Queued: insert ``custody_recheck`` trigger (mode=single) so the scraper
       engine runs county ``_fetch_single_booking`` for full field refresh.

    POST body: { "booking_number": "...", "open_source": false }
    """
    body = await request.json() or {}
    booking_number = (body.get("booking_number") or "").strip()
    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)

    changed_by = (body.get("changed_by") or body.get("agent") or "dashboard_user").strip()
    now = datetime.now(timezone.utc)
    arrests = get_collection("arrests")

    doc = await arrests.find_one({"booking_number": booking_number})
    if not doc:
        return JSONResponse(
            {"error": f"No arrest record for booking {booking_number}"},
            status_code=404,
        )

    county = (doc.get("county") or "").strip()
    detail_url = (doc.get("detail_url") or doc.get("source_url") or "").strip()
    old_bond = doc.get("bond_amount")
    try:
        old_bond_f = float(old_bond or 0)
    except (TypeError, ValueError):
        old_bond_f = 0.0

    immediate: dict = {
        "attempted": bool(detail_url),
        "bond_found": None,
        "bond_updated": False,
        "status_hint": None,
        "error": None,
    }
    bond_result = None

    # ── Fast path: fetch booking page HTML and extract bond ─────────────────
    if detail_url:
        try:
            import httpx
            from core.first_appearance_watcher import _extract_bond_from_html

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            }
            async with httpx.AsyncClient(
                timeout=25.0,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(detail_url)

            if resp.status_code >= 400:
                immediate["error"] = f"Source returned HTTP {resp.status_code}"
            else:
                html = resp.text or ""
                found = float(_extract_bond_from_html(html) or 0)
                immediate["bond_found"] = found

                # Light status signals from page text
                lower = html.lower()
                if "released" in lower and "in custody" not in lower:
                    immediate["status_hint"] = "Released"
                elif "in custody" in lower or "confined" in lower or "incarcerated" in lower:
                    immediate["status_hint"] = "In Custody"

                if found > 0 and abs(found - old_bond_f) >= 0.01:
                    bond_result = await _apply_bond_amount_to_arrest(
                        booking_number,
                        found,
                        changed_by=changed_by,
                        note=f"Auto-extracted from booking sheet: {detail_url[:120]}",
                        source="source_page_refresh",
                    )
                    immediate["bond_updated"] = bool(
                        isinstance(bond_result, dict) and bond_result.get("success")
                    )
                elif found > 0:
                    immediate["bond_updated"] = False  # already current
                    bond_result = {
                        "success": True,
                        "old_amount": old_bond_f,
                        "new_amount": found,
                        "unchanged": True,
                    }

                # Touch last-checked markers
                await arrests.update_one(
                    {"booking_number": booking_number},
                    {"$set": {
                        "last_source_refresh_at": now.isoformat(),
                        "last_source_refresh_by": changed_by,
                        "last_source_refresh_url": detail_url,
                    }},
                )
        except Exception as exc:
            logger.warning("[refresh-from-source] immediate fetch failed for %s: %s", booking_number, exc)
            immediate["error"] = str(exc)[:200]
    else:
        immediate["error"] = "No detail_url on record — open Source when available, or wait for full county re-scrape"

    # ── Queue full single-booking recheck via scraper engine ────────────────
    trigger_id = None
    queue_message = None
    if county:
        try:
            triggers = get_collection("scraper_triggers")
            trigger_doc = {
                "county": county,
                "type": "custody_recheck",
                "mode": "single",
                "booking_number": booking_number,
                "requested_at": now,
                "status": "pending",
                "requested_by": "dashboard_source_refresh",
                "detail_url": detail_url,
            }
            ins = await triggers.insert_one(trigger_doc)
            trigger_id = str(ins.inserted_id)
            queue_message = (
                f"Full refresh queued for {county} / {booking_number}. "
                "Scraper engine typically finishes in 30–120s."
            )
        except Exception as exc:
            logger.warning("[refresh-from-source] queue failed: %s", exc)
            queue_message = f"Could not queue scraper recheck: {exc}"
    else:
        queue_message = "No county on record — cannot queue county scraper"

    new_amount = None
    if isinstance(bond_result, dict) and bond_result.get("success"):
        new_amount = bond_result.get("new_amount")

    return {
        "success": True,
        "booking_number": booking_number,
        "county": county,
        "detail_url": detail_url,
        "old_bond_amount": old_bond_f,
        "new_bond_amount": new_amount if new_amount is not None else old_bond_f,
        "immediate": immediate,
        "bond_result": bond_result if isinstance(bond_result, dict) else None,
        "trigger_id": trigger_id,
        "queue_message": queue_message,
        "message": (
            f"Bond updated to ${new_amount:,.0f} from source page"
            if immediate.get("bond_updated") and new_amount
            else (
                f"Source checked — bond still ${old_bond_f:,.0f}"
                if immediate.get("bond_found") is not None and not immediate.get("error")
                else "Refresh requested"
            )
        ),
    }


@legacy_bp.get("/leads/refresh-from-source/status")
async def refresh_from_source_status(
    trigger_id: str = Query(default=""),
    booking_number: str = Query(default=""),
):
    """Poll scraper queue + current arrest bond after a refresh."""
    trigger_id = (trigger_id or "").strip()
    booking_number = (booking_number or "").strip()
    out: dict = {"success": True}

    if trigger_id:
        try:
            from bson import ObjectId
            triggers = get_collection("scraper_triggers")
            tdoc = await triggers.find_one({"_id": ObjectId(trigger_id)})
            if tdoc:
                out["trigger"] = {
                    "status": tdoc.get("status"),
                    "total_checked": tdoc.get("total_checked"),
                    "changes_found": tdoc.get("changes_found"),
                    "not_found_count": tdoc.get("not_found_count"),
                    "message": tdoc.get("message"),
                    "error": tdoc.get("error"),
                    "fallback": tdoc.get("fallback"),
                }
                booking_number = booking_number or tdoc.get("booking_number", "")
                # Pull recheck diffs
                rechecks = get_collection("custody_rechecks")
                diffs = []
                async for row in rechecks.find(
                    {"trigger_id": trigger_id},
                    {"_id": 0, "changes": 1, "source_found": 1, "booking_number": 1, "full_name": 1},
                ).limit(20):
                    diffs.append(row)
                out["rechecks"] = diffs
        except Exception as exc:
            out["trigger_error"] = str(exc)[:200]

    if booking_number:
        arrests = get_collection("arrests")
        doc = await arrests.find_one(
            {"booking_number": booking_number},
            {"bond_amount": 1, "bond_type": 1, "status": 1, "lead_score": 1, "lead_status": 1,
             "charges": 1, "last_custody_recheck": 1, "last_source_refresh_at": 1},
        )
        if doc:
            out["arrest"] = {
                "booking_number": booking_number,
                "bond_amount": doc.get("bond_amount", 0),
                "bond_type": doc.get("bond_type", ""),
                "status": doc.get("status", ""),
                "lead_score": doc.get("lead_score"),
                "lead_status": doc.get("lead_status"),
                "charges": doc.get("charges", ""),
                "last_custody_recheck": doc.get("last_custody_recheck"),
                "last_source_refresh_at": doc.get("last_source_refresh_at"),
            }

    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles iMessage Outreach Proxy
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.get("/imessage/status")
async def imessage_status():
    """Check status of all configured BlueBubbles servers."""
    import httpx

    if not BB_SERVERS:
        init_bluebubbles()  # Re-try loading config

    if not BB_SERVERS:
        return {"connected": False, "servers": [], "reason": "No BlueBubbles servers configured in .env"}

    servers = []
    any_connected = False
    any_private_api = False
    for phone_key, srv in BB_SERVERS.items():
        entry = {"phone": phone_key, "label": srv["label"], "email": srv["email"], "connected": False}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{srv['url']}/api/v1/server/info",
                    params={"password": srv["password"]},
                    headers={
                        "ngrok-skip-browser-warning": "true",
                        "User-Agent": "ShamrockLeads-Dashboard/1.0 (BlueBubbles-Client)",
                        "Accept": "application/json",
                    },
                    timeout=5,
                )
                data = r.json()
                if r.status_code == 200:
                    entry["connected"] = True
                    entry["private_api"] = data.get("data", {}).get("private_api", False)
                    entry["os_version"] = data.get("data", {}).get("os_version", "")
                    any_connected = True
                    if entry.get("private_api"):
                        any_private_api = True
        except Exception:
            entry["error"] = "unreachable"
        servers.append(entry)

    return {
        "connected": any_connected,
        "private_api": any_private_api,
        "server_count": len(BB_SERVERS),
        "servers": servers,
    }


@legacy_bp.post("/imessage/send")
async def imessage_send(request: Request):
    """Send an iMessage via BlueBubbles server."""
    import httpx

    if not BB_SERVERS:
        init_bluebubbles()

    if not BB_SERVERS:
        return JSONResponse({"error": "No BlueBubbles servers configured. Set BLUEBUBBLES_URL_0178 and BLUEBUBBLES_PASSWORD_0178 in .env"}, status_code=503)

    body = await request.json()
    phone_raw = body.get("phone", "")
    message = body.get("message", "").strip()
    booking_number = body.get("booking_number", "")
    defendant_name = body.get("defendant_name", "")
    county = body.get("county", "")
    recipient_label = body.get("recipient_label", "Unknown")
    agent_name = body.get("agent_name", "Brendan")
    from_number = body.get("from_number", "2399550178")

    if not phone_raw or not message:
        return JSONResponse({"error": "phone and message are required"}, status_code=400)

    phone = format_phone(phone_raw)
    if not phone:
        return JSONResponse({"error": f"Invalid phone number: {phone_raw}"}, status_code=400)

    srv = get_bb_server(from_number)
    if not srv:
        return JSONResponse({"error": f"No BlueBubbles server configured for {from_number}"}, status_code=503)

    # NOTE: Geo-tracking links are NOT auto-appended to outbound messages.
    # Use /api/tracking/<booking>/send-geo-link for explicit geo-link delivery.
    # This prevents the internal dashboard URL from leaking to clients.

    chat_guid = f"any;-;{phone}"
    temp_guid = f"shamrock-{uuid.uuid4().hex[:16]}"
    imessage_outreach = get_collection("imessage_outreach")

    # ── Dedup Guard: block duplicate outreach within 24h ──
    cooldown_hours = body.get("cooldown_hours", 24)
    if booking_number and not body.get("force_send", False):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
        recent = await imessage_outreach.find_one({
            "recipient_phone": phone,
            "booking_number": booking_number,
            "status": "sent",
            "direction": {"$ne": "inbound"},
            "sent_at": {"$gte": cutoff},
        }, {"_id": 0, "sent_at": 1, "message": 1})
        if recent:
            return JSONResponse(status_code=409, content={
                "success": False,
                "error": "duplicate",
                "detail": f"Already messaged this number for booking {booking_number} within {cooldown_hours}h",
                "last_sent": recent.get("sent_at"),
                "message_preview": (recent.get("message", ""))[:80],
            })

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{srv['url']}/api/v1/message/text",
                params={"password": srv["password"]},
                json={
                    "chatGuid": chat_guid,
                    "tempGuid": temp_guid,
                    "message": message,
                },
                timeout=15,
            )
            bb_resp = r.json()
            success = r.status_code in (200, 201)

        # Extract BB message GUID for unsend/edit capability
        bb_message_guid = ""
        if success:
            bb_data = bb_resp.get("data", {})
            if isinstance(bb_data, dict):
                bb_message_guid = bb_data.get("guid", "")

        # Log to MongoDB
        doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "recipient_phone": phone,
            "recipient_label": recipient_label,
            "message": message,
            "chat_guid": chat_guid,
            "temp_guid": temp_guid,
            "bb_message_guid": bb_message_guid,
            "direction": "outbound",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent" if success else "failed",
            "bb_status_code": r.status_code,
            "bb_error": bb_resp.get("message", "") if not success else "",
            "sent_by": "dashboard",
            "agent_name": agent_name,
            "from_number": from_number,
            "from_email": srv.get("email", ""),
        }
        await imessage_outreach.insert_one(doc)
        doc.pop("_id", None)

        if success:
            return {"success": True, "record": doc}
        else:
            return JSONResponse({"success": False, "error": bb_resp.get("message", "BlueBubbles error"), "record": doc}, status_code=502)

    except httpx.ConnectError:
        return JSONResponse({"error": "Cannot reach BlueBubbles server. Is it running?"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@legacy_bp.get("/imessage/history/{booking_number}")
async def imessage_history(booking_number):
    """Get outreach message history for a defendant."""
    imessage_outreach = get_collection("imessage_outreach")
    docs = []
    async for doc in imessage_outreach.find(
        {"booking_number": booking_number},
        {"_id": 0},
    ).sort("sent_at", -1).limit(50):
        docs.append(doc)
    return {"messages": docs, "count": len(docs)}


@legacy_bp.get("/imessage/templates")
async def imessage_templates():
    """Return available outreach message templates."""
    templates = [
        {
            "id": "standard",
            "name": "Standard Introduction",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. I see that {name} is currently in custody in the {county} County Jail. We can help get them home fast with flexible payment plans. Give us a call or reply here.",
        },
        {
            "id": "urgent",
            "name": "Urgent — Significant Bond",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. {name} is currently being held in {county} County on a significant bond. We specialize in quick releases and flexible payment options. Would you like help?",
        },
        {
            "id": "followup",
            "name": "Follow-Up",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds, just following up about {name} in {county} County. We're still available to help if you'd like to get them home. No obligation to chat.",
        },
        {
            "id": "payment",
            "name": "Payment Plan Offer",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. We can help bond {name} out of {county} County Jail today. We offer flexible payment plans and fast service. Reply or call us anytime.",
        },
    ]
    return {"templates": templates}