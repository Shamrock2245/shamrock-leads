"""
Stage one SwipeSimple invoice per financed installment on its due date.

The invoice reference is ``{booking}-I{n}`` and the amount is that row's
``amount_cents``. This does not call ``create_locked_invoice`` (that path is
one invoice per bond, for the down payment).

``SWIPESIMPLE_LIVE`` off: write a dry-run marker and do not call SwipeSimple.
``SWIPESIMPLE_LIVE`` on without a session: leave the row unstaged so the next
run can retry. ``SWIPESIMPLE_DISPATCH_LIVE`` off: log the customer send and
do not message anyone. A second run the same day does not stage again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from dashboard.extensions import get_collection
from dashboard.services import swipesimple_invoice_service as ss
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

CLAIMS_COLLECTION = "installment_invoice_claims"


def _today(now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _scope(query: dict) -> Optional[dict]:
    if not multi_tenant_enabled():
        return query
    tenant = current_tenant_id()
    if not tenant:
        return None
    return {**query, "tenant_id": tenant}


def _staff_test(plan: dict, booking: str) -> bool:
    if plan.get("is_test") is True or plan.get("test_case") is True:
        return True
    return str(booking or "").strip().upper().startswith("TEST-")


def _claim_id(plan_id: str, number: int) -> str:
    if not multi_tenant_enabled():
        return f"{plan_id}:{number}"
    return f"{current_tenant_id() or ''}:{plan_id}:{number}"


async def _patch_installment(plan_id: str, number: int, patch: dict) -> None:
    plans = get_collection("payment_plans")
    fresh = await plans.find_one({"plan_id": plan_id}) or {}
    rows = fresh.get("installments")
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if row_n != number:
            continue
        if row.get("status") in {"paid", "reversed"}:
            row["invoice_key"] = patch.get("invoice_key") or row.get("invoice_key")
        else:
            row.update(patch)
        changed = True
    if changed:
        await plans.update_one({"plan_id": plan_id}, {"$set": {"installments": rows}})


async def _bond_for(booking: str) -> dict:
    for name in ("bond_cases", "active_bonds"):
        try:
            doc = await get_collection(name).find_one({"booking_number": booking})
        except Exception:
            doc = None
        if doc:
            return doc
    return {}


async def _dispatch(plan: dict, link: str, number: int) -> dict:
    """Customer message. Dry-run unless the dispatch flag is on and the link is real."""
    plan_id = plan.get("plan_id")
    if not ss.dispatch_live_enabled() or not str(link or "").startswith("http"):
        logger.info(
            "[installment_invoice] dispatch dry-run plan_id=%s n=%s",
            plan_id,
            number,
        )
        return {"dispatched": False, "dry_run": True}
    phone = ""
    bond = await _bond_for(str(plan.get("booking_number") or ""))
    for source in (bond, plan):
        for key in ("indemnitor_phone", "indemnitor_cell", "defendant_phone"):
            raw = str(source.get(key) or "").strip()
            if raw:
                phone = raw
                break
        if phone:
            break
    if not phone:
        logger.info(
            "[installment_invoice] dispatch skipped no phone plan_id=%s n=%s",
            plan_id,
            number,
        )
        return {"dispatched": False, "dry_run": False, "reason": "no_phone"}
    cents = 0
    for row in plan.get("installments") or []:
        if isinstance(row, dict) and int(row.get("n") or 0) == number:
            cents = int(row.get("amount_cents") or 0)
    dollars = f"{cents / 100:.2f}"
    body = (
        f"Shamrock Bail Bonds: installment {number} of ${dollars} is ready to pay.\n"
        f"{link}\n"
        f"(239) 332-2245"
    )
    from dashboard.services.bb_client import send_message_universal

    await send_message_universal(phone, body)
    logger.info("[installment_invoice] dispatched plan_id=%s n=%s", plan_id, number)
    return {"dispatched": True, "dry_run": False}


async def _stage_one(plan: dict, row: dict, now: datetime) -> str:
    """Return staged, skipped, failed, or blocked. A lost race is skipped."""
    from pymongo.errors import DuplicateKeyError

    try:
        number = int(row.get("n") or 0)
        cents = int(row.get("amount_cents") or 0)
    except (TypeError, ValueError):
        return "skipped"
    if number <= 0 or cents <= 0:
        return "skipped"
    plan_id = str(plan.get("plan_id") or "")
    booking = str(plan.get("booking_number") or "").strip()
    if not plan_id or not booking:
        return "skipped"
    if row.get("invoice_key") or row.get("status") not in {"scheduled", "invoiced"}:
        return "skipped"
    reference = f"{booking}-I{number}"
    claims = get_collection(CLAIMS_COLLECTION)
    claim_id = _claim_id(plan_id, number)
    existing = await claims.find_one({"_id": claim_id})
    if existing:
        return "skipped"
    claim = {
        "_id": claim_id,
        "plan_id": plan_id,
        "installment_n": number,
        "booking_number": booking,
        "reference": reference,
        "amount_cents": cents,
        "status": "staging",
        "created_at": now.isoformat(),
    }
    if multi_tenant_enabled():
        claim["tenant_id"] = current_tenant_id()
    try:
        await claims.insert_one(claim)
    except DuplicateKeyError:
        return "skipped"

    live = ss.live_http_enabled()
    if live and _staff_test(plan, booking) and not ss._env_on(ss._TEST_CASE_INVOICES_ENV):
        await claims.update_one(
            {"_id": claim_id},
            {"$set": {"status": "staff_test_blocked"}},
        )
        logger.info(
            "[installment_invoice] staff test blocked plan_id=%s n=%s",
            plan_id,
            number,
        )
        return "blocked"
    if not live:
        await claims.update_one(
            {"_id": claim_id},
            {"$set": {"status": "dry_run", "dispatched": False}},
        )
        await _patch_installment(plan_id, number, {
            "status": "invoiced",
            "invoice_key": reference,
            "invoice_amount_cents": cents,
            "invoice_dry_run": True,
            "invoice_staged_at": now.isoformat(),
        })
        logger.info(
            "[installment_invoice] dry-run plan_id=%s n=%s amount_cents=%s reference=%s",
            plan_id,
            number,
            cents,
            reference,
        )
        await _dispatch(plan, "", number)
        return "staged"

    cfg = ss.load_swipesimple_session_config()
    if not (cfg.get("has_session") or cfg.get("has_cookie_jar")):
        await claims.delete_one({"_id": claim_id, "status": "staging"})
        logger.warning(
            "[installment_invoice] fail-closed session missing plan_id=%s n=%s",
            plan_id,
            number,
        )
        return "failed"

    bond = await _bond_for(booking)
    progress: dict[str, Any] = {}
    try:
        created = await ss._share_invoice_http(
            booking_number=reference,
            premium=Decimal(cents) / Decimal(100),
            bond_id=str(plan.get("bond_case_id") or plan_id),
            bond=bond,
            cfg=cfg,
            progress=progress,
        )
    except ss.SwipeSimpleLiveDisabled:
        await claims.delete_one({"_id": claim_id, "status": "staging"})
        logger.warning("[installment_invoice] live gate closed plan_id=%s n=%s", plan_id, number)
        return "failed"
    except ss.SwipeSimpleInvoiceError as exc:
        if progress.get("create_posted"):
            await claims.update_one(
                {"_id": claim_id},
                {"$set": {"status": "unresolved", "error_code": str(exc).split(" ", 1)[0][:80]}},
            )
            logger.warning(
                "[installment_invoice] create outcome unknown plan_id=%s n=%s",
                plan_id,
                number,
            )
        else:
            await claims.delete_one({"_id": claim_id, "status": "staging"})
            logger.warning(
                "[installment_invoice] create not sent plan_id=%s n=%s code=%s",
                plan_id,
                number,
                str(exc).split(" ", 1)[0][:80],
            )
        return "failed"

    link = str((created or {}).get("payment_link") or "")
    dispatch = await _dispatch(plan, link, number)
    await claims.update_one(
        {"_id": claim_id},
        {"$set": {
            "status": "invoiced",
            "invoice_id": (created or {}).get("invoice_id") or "",
            "payment_link": link,
            "dispatched": bool(dispatch.get("dispatched")),
            "dispatch_dry_run": bool(dispatch.get("dry_run")),
        }},
    )
    patch = {
        "status": "invoiced",
        "invoice_key": reference,
        "invoice_amount_cents": cents,
        "invoice_dry_run": False,
        "invoice_staged_at": now.isoformat(),
    }
    if link.startswith("http"):
        patch["payment_link"] = link
    await _patch_installment(plan_id, number, patch)
    logger.info(
        "[installment_invoice] staged plan_id=%s n=%s amount_cents=%s",
        plan_id,
        number,
        cents,
    )
    return "staged"


async def stage_due_installments(now: Optional[datetime] = None) -> dict:
    """Stage every active premium-confirmation installment that is due today or earlier."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    today = _today(moment)
    query = _scope({"status": "active", "source": "premium_confirmation"})
    if query is None:
        return {"staged": 0, "skipped": 0, "failed": 0, "blocked": 0, "dry_run": not ss.live_http_enabled()}
    plans = get_collection("payment_plans")
    staged = skipped = failed = blocked = 0
    cursor = plans.find(query)
    async for plan in cursor:
        for row in plan.get("installments") or []:
            if not isinstance(row, dict):
                continue
            due = str(row.get("due_date") or "")
            if not due or due > today:
                continue
            if row.get("status") != "scheduled" or row.get("invoice_key"):
                skipped += 1
                continue
            try:
                outcome = await _stage_one(plan, row, moment)
            except Exception:
                logger.warning(
                    "[installment_invoice] stage failed plan_id=%s",
                    plan.get("plan_id"),
                )
                outcome = "failed"
            if outcome == "staged":
                staged += 1
            elif outcome == "failed":
                failed += 1
            elif outcome == "blocked":
                blocked += 1
            else:
                skipped += 1
    return {
        "staged": staged,
        "skipped": skipped,
        "failed": failed,
        "blocked": blocked,
        "dry_run": not ss.live_http_enabled(),
    }
