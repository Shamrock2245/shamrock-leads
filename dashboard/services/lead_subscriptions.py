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

from datetime import datetime, timezone
from typing import Any

from dashboard.extensions import (
    KEY_FL_COUNTY_LABELS,
    REGISTERED_COUNTIES,
    parse_registered_county,
    scraper_source_state,
)
from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_platform_job
from dashboard.tenancy.flag import multi_tenant_enabled

_MODES = frozenset({"shared", "exclusive"})


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


async def replace_subscriptions(tenant_id: str, payload: dict) -> dict:
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
    pairs = _pairs(docs)
    for row in cleaned:
        _conflicts(pairs, tenant_id, row)
    with bind_platform_job("lead_subscriptions"):
        col = await _tenants()
        await col.update_one({"tenant_id": tenant_id}, {"$set": {"lead_subscriptions": cleaned}})
    found["lead_subscriptions"] = cleaned
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


def deliver_new_arrests(db: Any, records: list[Any]) -> list[str]:
    """Insert one lead pointer per subscriber. No defendant name or phone."""
    if not multi_tenant_enabled():
        return []
    routed = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for record in records:
        state = str(getattr(record, "State", "") or "FL").strip().upper() or "FL"
        county = str(getattr(record, "County", "") or "").strip()
        booking = str(getattr(record, "Booking_Number", "") or "").strip()
        if not county or not booking:
            continue
        arrest_id = f"{state}|{county}|{booking}"
        for tenant_id in subscribers_for(db, state=state, county=county):
            db["leads"].update_one(
                {"arrest_id": arrest_id, "tenant_id": tenant_id},
                {
                    "$setOnInsert": {
                        "tenant_id": tenant_id,
                        "arrest_id": arrest_id,
                        "state": state,
                        "county": county,
                        "booking_number": booking,
                        "routed_at": now,
                    }
                },
                upsert=True,
            )
            routed.append(tenant_id)
    return routed


def fan_out_if_enabled(db: Any, records: list[Any]) -> list[str]:
    if not multi_tenant_enabled():
        return []
    return deliver_new_arrests(db, records)
