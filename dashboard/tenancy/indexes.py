"""Tenant index specifications. Defined here, never created at startup.

Applying these on the live cluster is a later, explicit maintenance step.
Creating a unique (tenant_id, …) index before the backfill would treat every
legacy document's missing tenant_id as null and collide. The partial filter
below excludes documents that do not yet have a string tenant_id, so a future
apply is safe before or after the backfill — but this module does not apply it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dashboard.tenancy.constants import (
    AUDIT_RETENTION_TARGET_SECONDS,
    AUDIT_TTL_CURRENT_SECONDS,
)

# Indexes only documents that already carry a string tenant_id.
def _default_partial() -> dict:
    return {"tenant_id": {"$type": "string"}}


PARTIAL_TENANT = _default_partial()


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    keys: tuple[tuple[str, int], ...]
    name: str
    unique: bool = False
    partial: dict | None = field(default_factory=_default_partial)

    def as_create_kwargs(self) -> dict:
        kwargs = {"name": self.name, "unique": self.unique}
        if self.partial:
            kwargs["partialFilterExpression"] = self.partial
        return kwargs


def tenant_index_specs() -> tuple[IndexSpec, ...]:
    """Compound keys that replace today's global unique indexes.

    Existing creators, left untouched so production boot does not change:
    - dashboard/extensions.py poa_inventory.poa_number unique
    - dashboard/cron.py gcal dedupe, packet_id, plan_id, notification_id,
      defendant identity_key / defendant_id, recovery ids
    - scripts/mongo_indexes.py idx_poa_number_unique and idx_ttl_90d
    - writers/mongo_writer.py leads (arrest_id, tenant_id) unique ``dedup_lead``
      (the only tenant_id index that already exists; queries still do not filter)
    """
    return (
        IndexSpec("poa_inventory", (("tenant_id", 1), ("poa_number", 1)), "tenant_poa_number", True),
        IndexSpec(
            "active_bonds",
            (("tenant_id", 1), ("bond_case_id", 1)),
            "tenant_bond_case_id",
            True,
            {"tenant_id": {"$type": "string"}, "bond_case_id": {"$type": "string"}},
        ),
        IndexSpec("gcal_sync", (("tenant_id", 1), ("dedup_key", 1)), "tenant_gcal_dedup", True),
        IndexSpec(
            "paperwork_packets",
            (("tenant_id", 1), ("packet_id", 1)),
            "tenant_packet_id",
            True,
        ),
        IndexSpec("payment_plans", (("tenant_id", 1), ("plan_id", 1)), "tenant_plan_id", True),
        IndexSpec(
            "notifications",
            (("tenant_id", 1), ("notification_id", 1)),
            "tenant_notification_id",
            True,
        ),
        IndexSpec(
            "defendants",
            (("tenant_id", 1), ("defendant_id", 1)),
            "tenant_defendant_id",
            True,
        ),
        IndexSpec(
            "defendants",
            (("tenant_id", 1), ("identity_key", 1)),
            "tenant_identity_key",
            True,
        ),
        IndexSpec("leads", (("tenant_id", 1), ("arrest_id", 1)), "tenant_lead_arrest", True),
        IndexSpec(
            "recovery_case_shares",
            (("tenant_id", 1), ("share_id", 1)),
            "tenant_recovery_share",
            True,
        ),
        IndexSpec(
            "recovery_agents",
            (("tenant_id", 1), ("recovery_id", 1)),
            "tenant_recovery_agent",
            True,
        ),
        IndexSpec(
            "sub_agents",
            (("tenant_id", 1), ("license_number", 1)),
            "tenant_sub_agent_license",
            True,
        ),
        IndexSpec(
            "audit_events",
            (("tenant_id", 1), ("timestamp", -1)),
            "tenant_audit_timestamp",
            False,
        ),
    )


def audit_retention_policy() -> dict:
    """What production does today, and what the trust pack wants. Not applied."""
    return {
        "current_ttl_seconds": AUDIT_TTL_CURRENT_SECONDS,
        "current_index_names": ["idx_audit_ttl_90d", "idx_ttl_90d"],
        "current_sources": [
            "dashboard/cron.py",
            "scripts/mongo_indexes.py",
        ],
        "target_seconds_money_sign_poa": AUDIT_RETENTION_TARGET_SECONDS,
        "applied": False,
    }
