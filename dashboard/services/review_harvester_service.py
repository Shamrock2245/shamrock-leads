"""
ShamrockLeads — Google Review Harvester Service
==============================================
Identifies satisfied, eligible bond clients 2–30 days after bond posting or
discharge and sends a privacy-respecting Google review invitation via
BlueBubbles iMessage (with automatic SMS/RCS fallback).

Safety & Privacy Rules:
  - Defaults to mode="review" (queues staff review notifications; never auto-sends
    without explicit operator activation via Super CRM Automations).
  - Never includes defendant names, arrest details, or charge text in messages.
  - Excludes forfeited, FTA, warrant, rearrested, opted-out, or delinquent cases.
  - Dedupes sent requests by 10-digit phone number over 180 days.
  - In full_auto mode, sends only during daytime ET hours (10 AM – 7 PM ET).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_URL = "https://g.page/r/shamrockbailbonds/review"
ET_ZONE = ZoneInfo("America/New_York")
ELIGIBLE_STATUSES = frozenset({"posted", "active", "discharged", "exonerated", "completed"})


def _digits10(raw: Any) -> str:
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def _parse_dt(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _is_truthy(val: Any) -> bool:
    return val is True or str(val or "").strip().lower() in ("true", "1", "yes")


async def _consent_blocked(phone: str) -> bool:
    try:
        from dashboard.services.sms_consent_ledger import check_send_allowed
        blocked = await check_send_allowed(phone, "review_harvester")
        return bool(blocked)
    except Exception as exc:
        logger.error("[review-harvester] consent lookup failed: %s", type(exc).__name__)
        if os.getenv("BB_OPTOUT_GATE_FAIL_CLOSED", "").lower() in ("1", "true", "yes"):
            return True
        return False


def _has_negative_flag(bond: Dict[str, Any]) -> bool:
    status = str(bond.get("status") or "").strip().lower()
    if any(tok in status for tok in ("forfeit", "fta", "warrant", "estreature", "revoked", "surrender")):
        return True
    for flag in (
        "forfeited",
        "fta",
        "fta_flag",
        "warrant",
        "rearrested",
        "do_not_contact",
        "opt_out",
        "review_opt_out",
        "sms_opt_out",
    ):
        if _is_truthy(bond.get(flag)):
            return True
    return False


def build_review_message(first_name: str, review_url: str) -> str:
    """Privacy-safe, non-incentivized review invitation (zero arrest/charge PII)."""
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    return (
        f"{greeting} thank you for trusting Shamrock Bail Bonds! 🍀\n\n"
        f"If our team was helpful, sharing a quick Google review means a lot to our Fort Myers office:\n"
        f"{review_url}\n\n"
        f"Thank you. Reply STOP to opt out."
    )


class ReviewHarvesterService:
    """Scans posted/discharged bonds and requests Google reviews safely."""

    def __init__(self, db):
        self.db = db

    @property
    def review_log(self):
        return self.db["review_requests_log"]

    async def _is_delinquent(self, bond: Dict[str, Any]) -> bool:
        booking = str(bond.get("booking_number") or "").strip()
        bond_case_id = str(bond.get("bond_case_id") or "").strip()
        clauses = []
        if booking:
            clauses.append({"booking_number": booking})
        if bond_case_id:
            clauses.append({"bond_case_id": bond_case_id})
        if not clauses:
            return False
        try:
            delinquent = await self.db["payment_plans"].find_one(
                {
                    "$and": [
                        {"$or": clauses},
                        {
                            "$or": [
                                {"delinquent": True},
                                {"status": {"$in": ["delinquent", "defaulted", "collections"]}},
                            ]
                        },
                    ]
                }
            )
            return delinquent is not None
        except Exception:
            return False

    async def _fetch_candidate_bonds(self, limit: int) -> List[Dict[str, Any]]:
        seen_keys = set()
        rows: List[Dict[str, Any]] = []
        query = {"status": {"$in": list(ELIGIBLE_STATUSES)}}

        for coll_name in ("active_bonds", "bond_cases"):
            try:
                cursor = self.db[coll_name].find(query).sort("created_at", -1).limit(limit)
                async for doc in cursor:
                    dedup_id = (
                        str(doc.get("bond_case_id") or "").strip()
                        or str(doc.get("booking_number") or "").strip()
                        or str(doc.get("_id") or "")
                    )
                    if dedup_id and dedup_id in seen_keys:
                        continue
                    if dedup_id:
                        seen_keys.add(dedup_id)
                    rows.append(doc)
            except Exception:
                continue
        return rows

    async def _recently_logged(
        self,
        phone_key: str,
        *,
        mode: str,
        dedup_cutoff: datetime,
        review_cutoff: datetime,
    ) -> bool:
        sent_hit = await self.review_log.find_one({
            "phone_key": phone_key,
            "status": "sent",
            "created_at": {"$gte": dedup_cutoff},
        })
        if sent_hit:
            return True
        if mode != "full_auto":
            review_hit = await self.review_log.find_one({
                "phone_key": phone_key,
                "status": "review_queued",
                "created_at": {"$gte": review_cutoff},
            })
            if review_hit:
                return True
        return False

    async def scan_and_harvest(
        self,
        config: dict | None = None,
        *,
        now: datetime | None = None,
    ) -> dict:
        if config is None:
            from dashboard.services.automation_config import get_automation_config
            full_cfg = await get_automation_config(self.db)
            config = full_cfg.get("review_harvester", {})

        now = now or datetime.now(timezone.utc)
        mode = str(config.get("mode") or "review").strip().lower()
        send_client = mode == "full_auto"
        min_days = int(config.get("min_days_after_bond", 2))
        max_days = int(config.get("max_days_after_bond", 30))
        dedup_days = int(config.get("dedup_days", 180))
        max_per_cycle = int(config.get("max_per_cycle", 10))
        review_url = (
            str(config.get("review_url") or os.getenv("GOOGLE_REVIEW_URL") or DEFAULT_REVIEW_URL).strip()
        )
        if not review_url.lower().startswith("https://") or re.search(r"\s", review_url):
            return {
                "skipped": True,
                "reason": "invalid_review_url",
                "mode": mode,
            }

        if send_client and not config.get("ignore_quiet_hours"):
            et_hour = now.astimezone(ET_ZONE).hour
            if et_hour < 10 or et_hour >= 19:
                return {
                    "skipped": True,
                    "reason": "outside_daytime_hours",
                    "et_hour": et_hour,
                    "mode": mode,
                }

        dedup_cutoff = now - timedelta(days=dedup_days)
        review_cutoff = now - timedelta(days=7)

        results = {
            "scanned": 0,
            "sent": 0,
            "review_queued": 0,
            "skipped_window": 0,
            "skipped_ineligible": 0,
            "skipped_no_phone": 0,
            "skipped_opt_out": 0,
            "skipped_dedup": 0,
            "errors": 0,
            "mode": mode,
        }

        candidates = await self._fetch_candidate_bonds(max_per_cycle * 5)
        acted = 0
        seen_cycle_phones = set()

        for bond in candidates:
            results["scanned"] += 1
            if acted >= max_per_cycle:
                break

            if _has_negative_flag(bond) or await self._is_delinquent(bond):
                results["skipped_ineligible"] += 1
                continue

            ref_dt = (
                _parse_dt(bond.get("discharged_at"))
                or _parse_dt(bond.get("posted_at"))
                or _parse_dt(bond.get("bond_date"))
                or _parse_dt(bond.get("created_at"))
            )
            if not ref_dt:
                results["skipped_window"] += 1
                continue

            age_days = (now - ref_dt).total_seconds() / 86400.0
            if age_days < min_days or age_days > max_days:
                results["skipped_window"] += 1
                continue

            ind_obj = bond.get("indemnitor") if isinstance(bond.get("indemnitor"), dict) else {}
            raw_phone = (
                bond.get("indemnitor_phone")
                or ind_obj.get("phone")
                or bond.get("defendant_phone")
                or ""
            )
            phone_key = _digits10(raw_phone)
            if not phone_key:
                results["skipped_no_phone"] += 1
                continue

            if await _consent_blocked(str(raw_phone)):
                results["skipped_opt_out"] += 1
                continue

            if phone_key in seen_cycle_phones or await self._recently_logged(
                phone_key, mode=mode, dedup_cutoff=dedup_cutoff, review_cutoff=review_cutoff
            ):
                results["skipped_dedup"] += 1
                continue
            seen_cycle_phones.add(phone_key)

            raw_name = str(
                bond.get("indemnitor_name")
                or ind_obj.get("name")
                or ""
            ).strip()
            first_name = raw_name.split()[0] if raw_name else ""
            message = build_review_message(first_name, review_url)
            bond_case_id = str(bond.get("bond_case_id") or bond.get("booking_number") or "")

            try:
                if send_client:
                    from dashboard.services.bb_client import send_message_universal
                    send_res = await send_message_universal(
                        str(raw_phone),
                        message,
                        purpose="review_harvester",
                    )
                    if not send_res.get("success"):
                        results["errors"] += 1
                        continue
                    await self.review_log.insert_one({
                        "phone_key": phone_key,
                        "bond_case_id": bond_case_id,
                        "status": "sent",
                        "channel": "imessage",
                        "review_url": review_url,
                        "created_at": now,
                    })
                    results["sent"] += 1
                else:
                    await self._queue_review(bond_case_id=bond_case_id, phone_key=phone_key, message=message)
                    await self.review_log.insert_one({
                        "phone_key": phone_key,
                        "bond_case_id": bond_case_id,
                        "status": "review_queued",
                        "channel": "review",
                        "review_url": review_url,
                        "created_at": now,
                    })
                    results["review_queued"] += 1
                acted += 1
            except Exception as exc:
                logger.error("[review-harvester] Error for %s: %s", bond_case_id, exc)
                results["errors"] += 1

        return results

    async def _queue_review(self, *, bond_case_id: str, phone_key: str, message: str) -> None:
        try:
            from dashboard.routers.notifications import create_notification
            await create_notification(
                notification_type="review_harvester_review",
                title=f"⭐ Google Review Candidate ({bond_case_id or phone_key[-4:]})",
                message=f"Eligible contact ...{phone_key[-4:]}. Preview: {message}",
                entity_id=bond_case_id or phone_key,
                entity_type="review_harvester",
                metadata={"mode": "review", "phone_last4": phone_key[-4:]},
            )
        except Exception as exc:
            logger.debug("[review-harvester] review notification skipped: %s", exc)
