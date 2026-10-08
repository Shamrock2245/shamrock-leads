"""Collection classification and the Shamrock tenant id.

Global collections are an explicit allowlist. Every other name, including
names this list has not seen yet, is tenant-owned and fail-closed when the
flag is on. That is deliberate: a new collection must not leak across agencies
because somebody forgot to classify it.
"""

from __future__ import annotations

SHAMROCK_TENANT_ID = "shamrock"
TENANT_FIELD = "tenant_id"
BACKFILL_REV_FIELD = "tenant_backfill_rev"
BACKFILL_REV = 1

# Customer apps live here. Existing Shamrock hostnames stay on the apex domain
# and must not be parsed as a tenant slug.
SAAS_APP_SUFFIX = ".app.shamrockbailbonds.biz"

# Hosts that are Shamrock Bail Bonds today (tenant #1), not a customer subdomain.
SHAMROCK_HOSTS = frozenset(
    {
        "leads.shamrockbailbonds.biz",
        "paperwork.shamrockbailbonds.biz",
        "localhost",
        "127.0.0.1",
        "::1",
        "testserver",
        "178.156.179.237",
    }
)

# Public or platform-operational data. No tenant_id filter, even when the flag is on.
# Smaller than the competitive pass's "15" on purpose — ambiguous collections
# are tenant-owned instead of shared.
GLOBAL_COLLECTIONS = frozenset(
    {
        "arrests",  # public jail-roster facts, one scrape shared by the platform
        "scraper_status",
        "scraper_config",
        "scraper_run_log",
        "scraper_triggers",
        "ingestion_log",
        "error_log",
        "source_performance",
        "alpr_worker_status",
        "custody_rechecks",
        "zip_lookups",
    }
)

# Directory of tenants. Not readable through a normal agency request.
PLATFORM_COLLECTIONS = frozenset(
    {
        "tenants",
        "tenant_memberships",
    }
)

# Collection names referenced by application code (tests and one-off scripts
# excluded). dashboard/tenancy/inventory.py is the scan the tests enforce.
# Live Atlas may contain more; those are still tenant-scoped by default because
# they are not allowlisted, and the connected backfill stamps them too.
KNOWN_APP_COLLECTIONS = frozenset(
    {
        "accounting_imports",
        "active_bonds",
        "address_validations",
        "adobe_pdf_jobs",
        "alpr_worker_status",
        "app_logs",
        "arrests",
        "attorney_contacts",
        "audit_events",
        "automation_config",
        "automation_run_log",
        "bb_contact_syncs",
        "bb_group_chats",
        "bb_health_checks",
        "blog_publish_log",
        "bond_alerts",
        "bond_cases",
        "bond_checkins",
        "bonds",
        "booking_intake_previews",
        "buf_escrow_balances",
        "check_in_log",
        "check_in_requests",
        "check_ins",
        "collateral_items",
        "contacts",
        "conversion_events",
        "court_email_log",
        "court_outcomes",
        "court_reminders",
        "custody_rechecks",
        "defendant_notes",
        "defendants",
        "discharge_queue",
        "docket_events",
        "dnc_list",
        "document_deliveries",
        "domain_searches",
        "email_verifications",
        "enrichment_data",
        "error_log",
        "family_graph",
        "family_relationships",
        "family_trees",
        "financial_ledger",
        "forfeiture_remedies",
        "fta_alerts",
        "gas_event_log",
        "gcal_sync",
        "generated_reports",
        "geo_devices",
        "geo_events",
        "geo_pings",
        "geo_vehicle_watch",
        "geo_zones",
        "imessage_conversations",
        "imessage_outreach",
        "indemnitors",
        "ingestion_log",
        "intake_fanout_outbox",
        "intake_queue",
        "intake_recovery_log",
        "intakes",
        "leads",
        "lee_county_config",
        "lee_county_outreach_log",
        "lpr_hits",
        "lpr_watchlist",
        "matches",
        "missed_payment_alerts",
        "notifications",
        "osint_profiles",
        "osint_scans",
        "osint_trape_sessions",
        "outbound_messages",
        "outreach_config",
        "outreach_messages",
        "outreach_queue",
        "outreach_review_queue",
        "outreach_sequences",
        "paperwork_chase_log",
        "paperwork_packets",
        "paperwork_rules",
        "payment_dispatches",
        "payment_plans",
        "payments",
        "persons",
        "phone_validations",
        "poa_inventory",
        "portal_pins",
        "portal_tokens",
        "prospective_bonds",
        "rearrest_alerts",
        "rearrest_notifications",
        "recovery_agents",
        "recovery_case_documents",
        "recovery_case_notes",
        "recovery_case_shares",
        "scheduled_messages",
        "scraper_config",
        "scraper_run_log",
        "scraper_status",
        "scraper_triggers",
        "sms_consent_ledger",
        "social_accounts",
        "social_budget",
        "social_queue",
        "source_performance",
        "sub_agents",
        "swipesimple_invoice_claims",
        "swipesimple_session_health",
        "system_config",
        "tasks",
        "transactions",
        "wix_sync_log",
        "zip_lookups",
    }
)

TENANT_OWNED_COLLECTIONS = frozenset(
    name for name in KNOWN_APP_COLLECTIONS if name not in GLOBAL_COLLECTIONS
)

# Current production TTL, re-asserted on dashboard boot. Do not drop it here.
# dashboard/cron.py idx_audit_ttl_90d and scripts/mongo_indexes.py idx_ttl_90d.
AUDIT_TTL_CURRENT_SECONDS = 90 * 24 * 3600  # 7776000
# Trust-pack target for money, signature, and POA audit rows. Not applied.
AUDIT_RETENTION_TARGET_SECONDS = 7 * 365 * 24 * 3600


def is_global_collection(name: str) -> bool:
    return name in GLOBAL_COLLECTIONS


def is_platform_collection(name: str) -> bool:
    return name in PLATFORM_COLLECTIONS


def is_tenant_owned(name: str) -> bool:
    """Unknown names are tenant-owned. Only the global allowlist is shared."""
    return name not in GLOBAL_COLLECTIONS and name not in PLATFORM_COLLECTIONS
