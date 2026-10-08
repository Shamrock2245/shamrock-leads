"""State and county lead subscriptions.

The scraped roster stays global. A lead row is written only for an agency
that subscribed to that county. Flag off: nothing here runs, and the arrest
writer is unchanged.

Shared is the default. An exclusive subscription is rejected when anyone else
already has that county, and a shared subscription is rejected when the county
is already exclusive. Fail-closed source contracts, including the Ohio pilot,
are not in the catalog.

Shamrock with no stored list is treated as the key Florida counties already
named in ``KEY_FL_COUNTY_LABELS``. That seed is not written by a scraper.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from dashboard.extensions import (
    KEY_FL_COUNTY_LABELS,
    REGISTERED_COUNTIES,
    parse_registered_county,
    scraper_source_state,
)
from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_job_tenant, bind_platform_job
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

_MODES = frozenset({"shared", "exclusive"})
LEAD_FANOUT_OUTBOX = "lead_fanout_outbox"
LEAD_COUNTY_CLAIMS = "lead_county_claims"
LEAD_FANOUT_MAX_ATTEMPTS = 5
LEAD_FANOUT_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)
LEAD_FANOUT_DEPTH_ALERT = 25
LEAD_FANOUT_AGE_ALERT_SECONDS = 3600
LEAD_FANOUT_ALERT_COOLDOWN = 1800
_alert_state = {"depth_at": None}


class LeadSubscriptionError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class LeadSubscriptionsDisabled(LeadSubscriptionError):
    def __init__(self):
        super().__init__("disabled")


def sellable_counties() -> list[dict[str, str]]:
    rows = []
    for label in REGISTERED_COUNTIES:
        state_name = scraper_source_state(label)
        if state_name == "fail_closed":
            continue
        county, state = parse_registered_county(label)
        if not state:
            continue
        rows.append(
            {
                "label": label,
                "county": county,
                "state": state,
                "source_state": state_name,
            }
        )
    return rows


def _sellable_key(state: str, county: str) -> dict[str, str] | None:
    for row in sellable_counties():
        if row["state"] == state and row["county"].lower() == county.lower():
            return row
    return None


def shamrock_seed() -> list[dict[str, Any]]:
    rows = []
    for label in KEY_FL_COUNTY_LABELS:
        county, state = parse_registered_county(label)
        if not state or _sellable_key(state, county) is None:
            continue
        rows.append(
            {
                "state": state,
                "county": county,
                "mode": "shared",
                "price_cents": None,
                "seed": True,
            }
        )
    return rows


def _clean_subscription(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LeadSubscriptionError("subscription_invalid")
    state = str(raw.get("state") or "").strip().upper()
    county = str(raw.get("county") or "").strip()
    mode = str(raw.get("mode") or "shared").strip().lower()
    if mode not in _MODES:
        raise LeadSubscriptionError("mode_invalid")
    match = _sellable_key(state, county)
    if match is None:
        raise LeadSubscriptionError("not_sellable")
    if "price_cents" in raw and raw.get("price_cents") is not None:
        try:
            price = int(raw.get("price_cents"))
        except (TypeError, ValueError):
            raise LeadSubscriptionError("price_invalid")
        if price < 0 or price > 10_000_000:
            raise LeadSubscriptionError("price_invalid")
    else:
        price = None
    return {
        "state": match["state"],
        "county": match["county"],
        "mode": mode,
        "price_cents": price,
        "seed": False,
    }


def effective_subscriptions(doc: dict | None) -> list[dict[str, Any]]:
    stored = None if doc is None else doc.get("lead_subscriptions")
    if stored is None and (doc or {}).get("tenant_id") == SHAMROCK_TENANT_ID:
        return shamrock_seed()
    if not isinstance(stored, list):
        return []
    return [row for row in stored if isinstance(row, dict)]


def _conflicts(existing: list[tuple[str, dict]], tenant_id: str, wanted: dict) -> None:
    others = [
        (owner, row)
        for owner, row in existing
        if owner != tenant_id
        and row.get("state") == wanted["state"]
        and str(row.get("county") or "").lower() == wanted["county"].lower()
    ]
    if wanted["mode"] == "exclusive" and others:
        raise LeadSubscriptionError("exclusive_taken")
    if any(row.get("mode") == "exclusive" for _, row in others):
        raise LeadSubscriptionError("exclusive_taken")


async def _tenants():
    from dashboard.extensions import get_collection

    return get_collection("tenants")


async def _all_docs() -> list[dict]:
    with bind_platform_job("lead_subscriptions"):
        col = await _tenants()
        cursor = col.find({})
        if hasattr(cursor, "__aiter__"):
            return [doc async for doc in cursor]
        return list(cursor)


def _pairs(docs: list[dict]) -> list[tuple[str, dict]]:
    pairs = []
    for doc in docs:
        tenant_id = doc.get("tenant_id") or ""
        for row in effective_subscriptions(doc):
            pairs.append((tenant_id, row))
    return pairs


async def catalog(state: str = "") -> dict[str, Any]:
    if not multi_tenant_enabled():
        raise LeadSubscriptionsDisabled()
    rows = sellable_counties()
    states = sorted({row["state"] for row in rows})
    chosen = state.strip().upper()
    if chosen:
        rows = [row for row in rows if row["state"] == chosen]
    return {"states": states, "counties": rows, "policy_default": "shared"}


async def matrix() -> dict[str, Any]:
    if not multi_tenant_enabled():
        raise LeadSubscriptionsDisabled()
    docs = await _all_docs()
    agencies = []
    seen = set()
    for doc in docs:
        tenant_id = doc.get("tenant_id")
        if not tenant_id:
            continue
        seen.add(tenant_id)
        agencies.append(
            {
                "tenant_id": tenant_id,
                "legal_name": doc.get("legal_name"),
                "seeded": doc.get("lead_subscriptions") is None and tenant_id == SHAMROCK_TENANT_ID,
                "subscriptions": effective_subscriptions(doc),
            }
        )
    if SHAMROCK_TENANT_ID not in seen:
        agencies.insert(
            0,
            {
                "tenant_id": SHAMROCK_TENANT_ID,
                "legal_name": "Shamrock Bail Bonds",
                "seeded": True,
                "subscriptions": shamrock_seed(),
            },
        )
    return {"agencies": agencies, "policy_default": "shared"}


def _county_key(row: dict) -> tuple[str, str]:
    return (str(row.get("state") or ""), str(row.get("county") or "").lower())


def _others_for(docs: list[dict], tenant_id: str, row: dict) -> list[tuple[str, dict]]:
    return [
        (owner, existing)
        for owner, existing in _pairs(docs)
        if owner != tenant_id and _county_key(existing) == _county_key(row)
    ]


async def _claim_collection():
    from dashboard.extensions import get_collection

    return get_collection(LEAD_COUNTY_CLAIMS)


async def _ensure_claim_index(claims) -> None:
    created = claims.create_index(
        [("state", 1), ("county", 1)],
        unique=True,
        name="lead_county_claim_unique",
    )
    if hasattr(created, "__await__"):
        await created


async def _join_claim(claims, existing: dict, tenant_id: str, mode: str, others: list[tuple[str, dict]]) -> None:
    holders = [str(item) for item in (existing.get("holders") or []) if item]
    other_holders = [item for item in holders if item != tenant_id]
    other_exclusive = any(row.get("mode") == "exclusive" for _, row in others) or (
        existing.get("mode") == "exclusive" and other_holders
    )
    if mode == "exclusive" and (other_holders or others):
        raise LeadSubscriptionError("exclusive_taken")
    if other_exclusive and tenant_id not in holders:
        raise LeadSubscriptionError("exclusive_taken")
    if mode == "exclusive":
        new_holders = [tenant_id]
        new_mode = "exclusive"
    else:
        if existing.get("mode") == "exclusive" and tenant_id not in holders:
            raise LeadSubscriptionError("exclusive_taken")
        new_holders = list(dict.fromkeys([*holders, tenant_id]))
        new_mode = "shared" if other_holders or others else mode
    result = await claims.update_one(
        {
            "state": existing["state"],
            "county": existing["county"],
            "holders": existing.get("holders") or [],
            "mode": existing.get("mode"),
        },
        {"$set": {"holders": new_holders, "mode": new_mode}},
    )
    if getattr(result, "matched_count", 1) == 0:
        raise LeadSubscriptionError("exclusive_taken")


async def _acquire_claim(claims, tenant_id: str, row: dict, docs: list[dict]) -> None:
    others = _others_for(docs, tenant_id, row)
    if row["mode"] == "exclusive" and others:
        raise LeadSubscriptionError("exclusive_taken")
    if any(existing.get("mode") == "exclusive" for _, existing in others):
        raise LeadSubscriptionError("exclusive_taken")
    existing = await claims.find_one({"state": row["state"], "county": row["county"]})
    if existing:
        await _join_claim(claims, existing, tenant_id, row["mode"], others)
        return
    holders = [tenant_id] if row["mode"] == "exclusive" else list(dict.fromkeys([*[owner for owner, _ in others], tenant_id]))
    mode = "exclusive" if row["mode"] == "exclusive" else ("shared" if others else row["mode"])
    try:
        await claims.insert_one(
            {
                "state": row["state"],
                "county": row["county"],
                "mode": mode,
                "holders": holders,
            }
        )
    except DuplicateKeyError:
        existing = await claims.find_one({"state": row["state"], "county": row["county"]})
        if not existing:
            raise LeadSubscriptionError("exclusive_taken")
        await _join_claim(claims, existing, tenant_id, row["mode"], others)


async def _release_claim(claims, tenant_id: str, row: dict) -> None:
    existing = await claims.find_one({"state": row["state"], "county": row["county"]})
    if not existing:
        return
    holders = [item for item in (existing.get("holders") or []) if item != tenant_id]
    if not holders:
        await claims.delete_one({"state": row["state"], "county": row["county"]})
        return
    await claims.update_one(
        {
            "state": existing["state"],
            "county": existing["county"],
            "holders": existing.get("holders") or [],
        },
        {"$set": {"holders": holders, "mode": "shared" if len(holders) > 1 else existing.get("mode") or "shared"}},
    )


async def _sync_claims(tenant_id: str, before: list[dict], cleaned: list[dict], docs: list[dict]) -> None:
    before_keys = {_county_key(row): row for row in before}
    after_keys = {_county_key(row): row for row in cleaned}
    with bind_platform_job("lead_subscriptions"):
        claims = await _claim_collection()
        await _ensure_claim_index(claims)
        for key, row in after_keys.items():
            if before_keys.get(key) == row and key in before_keys:
                existing = await claims.find_one({"state": row["state"], "county": row["county"]})
                if existing and tenant_id in (existing.get("holders") or []):
                    continue
            await _acquire_claim(claims, tenant_id, row, docs)
        for key, row in before_keys.items():
            if key not in after_keys:
                await _release_claim(claims, tenant_id, row)


def _subscription_state(rows: list[dict]) -> list[dict]:
    return [
        {
            "state": row.get("state"),
            "county": row.get("county"),
            "mode": row.get("mode"),
            "price_cents": row.get("price_cents"),
        }
        for row in rows
        if isinstance(row, dict)
    ]


async def _write_subscription_audit(
    *,
    tenant_id: str,
    actor: str,
    reason: str,
    old_state: list[dict],
    new_state: list[dict],
) -> None:
    from dashboard.extensions import get_collection

    event = {
        "event_id": str(uuid.uuid4()),
        "entity_type": "tenant_lead_subscriptions",
        "entity_id": tenant_id,
        "action": "lead_subscriptions_updated",
        "actor": actor or "platform",
        "reason": reason or "lead_subscription_update",
        "old_state": {"subscriptions": old_state},
        "new_state": {"subscriptions": new_state},
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with bind_job_tenant(tenant_id, job_name="lead_subscriptions"):
        col = get_collection("audit_events")
        await col.insert_one(event)


async def replace_subscriptions(tenant_id: str, payload: dict, *, actor: str = "", reason: str = "") -> dict:
    if not multi_tenant_enabled():
        raise LeadSubscriptionsDisabled()
    if not isinstance(payload, dict) or not isinstance(payload.get("subscriptions"), list):
        raise LeadSubscriptionError("subscription_invalid")
    cleaned = [_clean_subscription(item) for item in payload["subscriptions"]]
    keys = [(row["state"], row["county"].lower()) for row in cleaned]
    if len(keys) != len(set(keys)):
        raise LeadSubscriptionError("subscription_invalid")
    docs = await _all_docs()
    found = next((doc for doc in docs if doc.get("tenant_id") == tenant_id), None)
    if found is None:
        raise LeadSubscriptionError("not_found")
    before = effective_subscriptions(found)
    pairs = _pairs(docs)
    for row in cleaned:
        _conflicts(pairs, tenant_id, row)
    await _sync_claims(tenant_id, before, cleaned, docs)
    with bind_platform_job("lead_subscriptions"):
        col = await _tenants()
        await col.update_one({"tenant_id": tenant_id}, {"$set": {"lead_subscriptions": cleaned}})
    found["lead_subscriptions"] = cleaned
    await _write_subscription_audit(
        tenant_id=tenant_id,
        actor=actor,
        reason=reason or str(payload.get("reason") or "lead_subscription_update"),
        old_state=_subscription_state(before),
        new_state=_subscription_state(cleaned),
    )
    return {
        "tenant_id": tenant_id,
        "seeded": False,
        "subscriptions": cleaned,
    }


def subscribers_for(db: Any, *, state: str, county: str) -> list[str]:
    """Sync lookup used by the arrest writer. Fail-closed counties match nobody."""
    if _sellable_key(state, county) is None:
        return []
    docs = list(db["tenants"].find({}, {"tenant_id": 1, "lead_subscriptions": 1, "legal_name": 1}))
    found = []
    for doc in docs:
        tenant_id = doc.get("tenant_id")
        if not tenant_id:
            continue
        for row in effective_subscriptions(doc):
            if row.get("state") == state and str(row.get("county") or "").lower() == county.lower():
                found.append(tenant_id)
                break
    return found


def _record_pointer(record: Any) -> dict[str, str] | None:
    """Booking pointer only. Never copy a name, phone, or address."""
    state = str(getattr(record, "State", "") or "FL").strip().upper() or "FL"
    county = str(getattr(record, "County", "") or "").strip()
    booking = str(getattr(record, "Booking_Number", "") or "").strip()
    if not county or not booking:
        return None
    return {
        "arrest_id": f"{state}|{county}|{booking}",
        "state": state,
        "county": county,
        "booking_number": booking,
    }


def enqueue_lead_fanout(db: Any, records: list[Any]) -> int:
    """Queue pointers so a failed tenant write can be retried. Flag off writes nothing."""
    if not multi_tenant_enabled():
        return 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    queued = 0
    for record in records:
        pointer = _record_pointer(record)
        if pointer is None:
            continue
        db[LEAD_FANOUT_OUTBOX].update_one(
            {"arrest_id": pointer["arrest_id"], "status": "pending"},
            {
                "$setOnInsert": {
                    "arrest_id": pointer["arrest_id"],
                    "state": pointer["state"],
                    "county": pointer["county"],
                    "booking_number": pointer["booking_number"],
                    "status": "pending",
                    "attempts": 0,
                    "next_attempt_at": now,
                    "enqueued_at": now,
                    "last_error": "",
                }
            },
            upsert=True,
        )
        queued += 1
    return queued


def _write_lead_pointer(db: Any, tenant_id: str, pointer: dict[str, str], now: str) -> None:
    db["leads"].update_one(
        {"arrest_id": pointer["arrest_id"], "tenant_id": tenant_id},
        {
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "arrest_id": pointer["arrest_id"],
                "state": pointer["state"],
                "county": pointer["county"],
                "booking_number": pointer["booking_number"],
                "routed_at": now,
            }
        },
        upsert=True,
    )


def _deliver_pointer(db: Any, pointer: dict[str, str]) -> tuple[list[str], bool]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    routed: list[str] = []
    failed = False
    for tenant_id in subscribers_for(db, state=pointer["state"], county=pointer["county"]):
        try:
            _write_lead_pointer(db, tenant_id, pointer, now)
            routed.append(tenant_id)
        except Exception:
            logger.exception("lead fan-out tenant write failed")
            failed = True
    return routed, failed


def deliver_new_arrests(db: Any, records: list[Any]) -> list[str]:
    """Insert one lead pointer per subscriber. A failed write is queued, not dropped."""
    if not multi_tenant_enabled():
        return []
    routed: list[str] = []
    for record in records:
        pointer = _record_pointer(record)
        if pointer is None:
            continue
        written, failed = _deliver_pointer(db, pointer)
        routed.extend(written)
        if failed:
            enqueue_lead_fanout(db, [record])
    return routed


def fan_out_if_enabled(db: Any, records: list[Any]) -> list[str]:
    if not multi_tenant_enabled():
        return []
    try:
        return deliver_new_arrests(db, records)
    except Exception:
        logger.exception("lead fan-out failed; queued for retry")
        try:
            enqueue_lead_fanout(db, records)
        except Exception:
            logger.exception("lead fan-out outbox enqueue failed")
        return []


def record_lead_fanout(db: Any, records: list[Any]) -> list[str]:
    """Arrest-writer entry. If fan-out raises, the pointer is still queued."""
    if not multi_tenant_enabled():
        return []
    try:
        return fan_out_if_enabled(db, records)
    except Exception:
        logger.exception("lead fan-out skipped")
        try:
            enqueue_lead_fanout(db, records)
        except Exception:
            logger.exception("lead fan-out outbox enqueue failed")
        return []


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _collect(cursor) -> list:
    if hasattr(cursor, "__aiter__"):
        return [doc async for doc in cursor]
    return list(cursor)


async def _finish(value):
    if hasattr(value, "__await__"):
        return await value
    return value


def _open_status(row: dict) -> bool:
    return row.get("status") in {"pending", "failed"}


def _due(row: dict, now: datetime) -> bool:
    if not _open_status(row):
        return False
    nxt = _parse_time(row.get("next_attempt_at"))
    if nxt is None:
        return True
    return nxt <= now


def _age_seconds(row: dict, now: datetime) -> float:
    stamped = _parse_time(row.get("enqueued_at"))
    if stamped is None:
        return 0
    return max(0, (now - stamped).total_seconds())


async def _alert_outbox(*, dead: int, depth: int, oldest_seconds: float, arrest_ids: list[str]) -> bool:
    """Ops alert on the existing alerts webhook. Counts and booking pointers only."""
    minutes = int(oldest_seconds // 60)
    lines = [
        "Lead fan-out outbox needs attention.",
        f"Dead-lettered this sweep: {dead}.",
        f"Open rows: {depth}.",
        f"Oldest open age: {minutes} min.",
    ]
    if arrest_ids:
        lines.append("Dead pointers: " + ", ".join(arrest_ids[:5]))
    lines.append("Booking pointers only.")
    from dashboard.services.automation_digest import post_slack

    return await post_slack("\n".join(lines), webhook_env="SLACK_WEBHOOK_ALERTS")


async def retry_lead_fanout(db: Any) -> dict[str, Any]:
    """Retry due outbox rows. Flag off does not read the outbox or alert.

    A failed write stays ``failed`` until its backoff elapses. After
    ``LEAD_FANOUT_MAX_ATTEMPTS`` the row becomes ``dead`` and is not retried.
    """
    if not multi_tenant_enabled():
        return {"delivered": 0, "failed": 0, "dead": 0, "pending": 0, "alerted": False}
    col = db[LEAD_FANOUT_OUTBOX]
    now = datetime.now(timezone.utc)
    delivered = 0
    failed = 0
    dead = 0
    dead_ids: list[str] = []
    for row in await _collect(col.find({})):
        if not isinstance(row, dict) or not _due(row, now):
            continue
        pointer = {
            "arrest_id": str(row.get("arrest_id") or ""),
            "state": str(row.get("state") or ""),
            "county": str(row.get("county") or ""),
            "booking_number": str(row.get("booking_number") or ""),
        }
        if not pointer["arrest_id"] or not pointer["county"] or not pointer["booking_number"]:
            continue
        _written, write_failed = _deliver_pointer(db, pointer)
        stamp = now.replace(microsecond=0).isoformat()
        if not write_failed:
            await _finish(col.update_one(
                {"arrest_id": pointer["arrest_id"], "status": row.get("status")},
                {"$set": {"status": "delivered", "updated_at": stamp, "last_error": ""}},
            ))
            delivered += 1
            continue
        attempts = int(row.get("attempts") or 0) + 1
        if attempts >= LEAD_FANOUT_MAX_ATTEMPTS:
            status = "dead"
            dead += 1
            dead_ids.append(pointer["arrest_id"])
            nxt = stamp
        else:
            status = "failed"
            failed += 1
            delay = LEAD_FANOUT_BACKOFF_SECONDS[min(attempts - 1, len(LEAD_FANOUT_BACKOFF_SECONDS) - 1)]
            nxt = (now + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
        await _finish(col.update_one(
            {"arrest_id": pointer["arrest_id"], "status": row.get("status")},
            {"$set": {
                "status": status,
                "attempts": attempts,
                "next_attempt_at": nxt,
                "last_error": "tenant_write_failed",
                "updated_at": stamp,
            }},
        ))
    open_rows = [
        row for row in await _collect(col.find({}))
        if isinstance(row, dict) and _open_status(row)
    ]
    depth = len(open_rows)
    oldest = max((_age_seconds(row, now) for row in open_rows), default=0)
    alert = False
    if dead:
        alert = True
    elif depth >= LEAD_FANOUT_DEPTH_ALERT or oldest >= LEAD_FANOUT_AGE_ALERT_SECONDS:
        last = _alert_state.get("depth_at")
        if last is None or (now - last).total_seconds() >= LEAD_FANOUT_ALERT_COOLDOWN:
            alert = True
            _alert_state["depth_at"] = now
    alerted = False
    if alert:
        alerted = await _alert_outbox(
            dead=dead,
            depth=depth,
            oldest_seconds=oldest,
            arrest_ids=dead_ids,
        )
    return {
        "delivered": delivered,
        "failed": failed,
        "dead": dead,
        "pending": depth,
        "alerted": bool(alerted),
    }
