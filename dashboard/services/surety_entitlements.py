"""Per-agency surety checklist. Platform templates stay in the registry.

Flag off: every helper returns today's registry behavior and does not read
Mongo. Flag on: Shamrock keeps OSI and Palmetto. Another agency can generate
paperwork only for active sureties a super-admin enabled. Inactive catalog
rows cannot be enabled. Private templates are a labeled slot for the
upload-and-map desk; this module does not render them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from dashboard.services.surety_registry import SURETY_REGISTRY, normalize_surety
from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_job_tenant, bind_platform_job, current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

SHAMROCK_SURETIES = frozenset({"osi", "palmetto"})


class SuretyEntitlementError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SuretyEntitlementsDisabled(SuretyEntitlementError):
    def __init__(self):
        super().__init__("disabled")


def _active_ids() -> set[str]:
    return {sid for sid, meta in SURETY_REGISTRY.items() if meta.get("active")}


def catalog_rows() -> list[dict[str, Any]]:
    rows = []
    for sid, meta in SURETY_REGISTRY.items():
        rows.append(
            {
                "id": sid,
                "label": meta["label"],
                "short": meta["short"],
                "active": bool(meta["active"]),
                "can_enable": bool(meta["active"]),
            }
        )
    return rows


def _clean_enabled(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SuretyEntitlementError("sureties_invalid")
    enabled = []
    for item in raw:
        sid = normalize_surety(item)
        if sid not in SURETY_REGISTRY:
            raise SuretyEntitlementError("surety_unknown")
        if not SURETY_REGISTRY[sid]["active"]:
            raise SuretyEntitlementError("surety_inactive")
        if sid not in enabled:
            enabled.append(sid)
    return enabled


def _clean_private(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SuretyEntitlementError("private_templates_invalid")
    rows = []
    for index, item in enumerate(raw[:10], start=1):
        if not isinstance(item, dict):
            raise SuretyEntitlementError("private_templates_invalid")
        label = str(item.get("label") or "").strip()
        if not label or len(label) > 80 or "\n" in label:
            raise SuretyEntitlementError("private_templates_invalid")
        rows.append({"id": f"private_{index}", "label": label, "status": "slot"})
    return rows


def access_view(doc: dict | None) -> dict[str, Any]:
    tenant_id = (doc or {}).get("tenant_id") or ""
    stored = (doc or {}).get("surety_access") or {}
    enabled = [sid for sid in (stored.get("enabled") or []) if sid in _active_ids()]
    if tenant_id == SHAMROCK_TENANT_ID:
        enabled = list(dict.fromkeys([*sorted(SHAMROCK_SURETIES), *enabled]))
    return {
        "tenant_id": tenant_id,
        "legal_name": (doc or {}).get("legal_name"),
        "enabled": enabled,
        "locked": sorted(SHAMROCK_SURETIES) if tenant_id == SHAMROCK_TENANT_ID else [],
        "private_templates": list(stored.get("private_templates") or []),
    }


async def _tenants():
    from dashboard.extensions import get_collection

    return get_collection("tenants")


async def _tenant_doc(tenant_id: str):
    try:
        with bind_platform_job("surety_entitlements"):
            col = await _tenants()
            return await col.find_one({"tenant_id": tenant_id})
    except (KeyError, LookupError):
        return None


async def entitled_ids() -> set[str]:
    """Surety ids this request may generate. Flag off is the active registry."""
    if not multi_tenant_enabled():
        return _active_ids()
    tenant_id = current_tenant_id()
    if not tenant_id:
        return set()
    doc = await _tenant_doc(tenant_id)
    extra = set()
    if doc:
        extra = {sid for sid in ((doc.get("surety_access") or {}).get("enabled") or []) if sid in _active_ids()}
    if tenant_id == SHAMROCK_TENANT_ID:
        return set(SHAMROCK_SURETIES) | extra
    return extra


async def assert_entitled(surety_id: str) -> None:
    if not multi_tenant_enabled():
        return
    sid = normalize_surety(surety_id)
    if sid not in await entitled_ids():
        raise SuretyEntitlementError("surety_not_entitled")


async def entitlement_denial(surety_id: str) -> dict[str, str] | None:
    """JSON body when this agency cannot generate that carrier. Flag off is None."""
    try:
        await assert_entitled(surety_id)
    except SuretyEntitlementError as exc:
        return {
            "success": False,
            "error": exc.code,
            "message": "This agency is not enabled for that surety.",
            "surety_id": str(surety_id or ""),
        }
    return None


async def annotate_picker(rows: list[dict]) -> list[dict]:
    if not multi_tenant_enabled():
        return rows
    allowed = await entitled_ids()
    annotated = []
    for row in rows:
        copy = dict(row)
        copy["selectable"] = bool(row.get("active") and row.get("id") in allowed)
        if row.get("active") and row.get("id") not in allowed:
            copy["reason"] = "Not enabled for this agency"
        annotated.append(copy)
    return annotated


async def list_access() -> dict[str, Any]:
    if not multi_tenant_enabled():
        raise SuretyEntitlementsDisabled()
    with bind_platform_job("surety_entitlements"):
        col = await _tenants()
        cursor = col.find({})
        docs = []
        if hasattr(cursor, "__aiter__"):
            async for doc in cursor:
                docs.append(doc)
        else:
            docs = list(cursor)
    agencies = [access_view(doc) for doc in docs]
    if not any(row["tenant_id"] == SHAMROCK_TENANT_ID for row in agencies):
        agencies.insert(0, access_view({"tenant_id": SHAMROCK_TENANT_ID, "legal_name": "Shamrock Bail Bonds"}))
    return {"catalog": catalog_rows(), "agencies": agencies}


def _access_state(view: dict) -> dict[str, Any]:
    return {
        "enabled": list(view.get("enabled") or []),
        "private_templates": list(view.get("private_templates") or []),
    }


async def _write_access_audit(
    *,
    tenant_id: str,
    actor: str,
    reason: str,
    old_state: dict,
    new_state: dict,
) -> None:
    from dashboard.extensions import get_collection

    event = {
        "event_id": str(uuid.uuid4()),
        "entity_type": "tenant_surety_access",
        "entity_id": tenant_id,
        "action": "surety_access_updated",
        "actor": actor or "platform",
        "reason": reason or "surety_access_update",
        "old_state": old_state,
        "new_state": new_state,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with bind_job_tenant(tenant_id, job_name="surety_entitlements"):
        col = get_collection("audit_events")
        await col.insert_one(event)


async def set_access(tenant_id: str, payload: dict, *, actor: str = "", reason: str = "") -> dict:
    if not multi_tenant_enabled():
        raise SuretyEntitlementsDisabled()
    if not isinstance(payload, dict):
        raise SuretyEntitlementError("sureties_invalid")
    enabled = _clean_enabled(payload.get("enabled"))
    private_templates = _clean_private(payload.get("private_templates"))
    if tenant_id == SHAMROCK_TENANT_ID:
        enabled = list(dict.fromkeys([*sorted(SHAMROCK_SURETIES), *enabled]))
    with bind_platform_job("surety_entitlements"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": tenant_id})
        if not found:
            raise SuretyEntitlementError("not_found")
        before = _access_state(access_view(found))
        access = {"enabled": enabled, "private_templates": private_templates}
        await col.update_one({"tenant_id": tenant_id}, {"$set": {"surety_access": access}})
        found["surety_access"] = access
    view = access_view(found)
    await _write_access_audit(
        tenant_id=tenant_id,
        actor=actor,
        reason=reason or str(payload.get("reason") or "surety_access_update"),
        old_state=before,
        new_state=_access_state(view),
    )
    return view
