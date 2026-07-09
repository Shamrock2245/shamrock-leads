"""
Node-RED / external cron pack for automation sweeps.

Keep this free of FastAPI imports so it can be tested and documented
without loading the full router stack.
"""
from __future__ import annotations

NODE_RED_SCHEDULE = [
    {
        "id": "lead-qualification-morning",
        "cron": "0 8 * * *",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/lead-qualification",
        "body": {"hours_back": 24, "hot_threshold": 70, "limit": 50},
        "slack": "#leads",
        "desc": "Morning hot/warm/high-value lead surface",
    },
    {
        "id": "bond-lifecycle-midday",
        "cron": "0 12 * * *",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/bond-lifecycle",
        "body": {"stuck_days": 3, "limit": 40},
        "slack": "#leads",
        "desc": "Missing court dates + stuck pipeline stages",
    },
    {
        "id": "risk-mitigation-evening",
        "cron": "0 17 * * *",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/risk-mitigation",
        "body": {"high_risk_threshold": 70, "court_hours": 48, "limit": 40},
        "slack": "#scraper-errors",
        "desc": "Flight risk, court-soon, forfeiture flags",
    },
    {
        "id": "court-email-scan",
        "cron": "*/15 * * * *",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/court-email-scan",
        "body": {"since_hours": 1},
        "slack": None,
        "desc": "Gmail court events (also runs in-process every 15m)",
    },
    {
        "id": "bond-report-weekly-osi",
        "cron": "0 7 * * 1",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/bond-report",
        "body": {"surety": "OSI", "include_discharges": True, "store": True},
        "slack": "#leads",
        "desc": "Monday OSI official bond XLSX",
    },
    {
        "id": "bond-report-weekly-palmetto",
        "cron": "5 7 * * 1",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/bond-report",
        "body": {"surety": "PALMETTO", "include_discharges": True, "store": True},
        "slack": "#leads",
        "desc": "Monday Palmetto official bond XLSX",
    },
    {
        "id": "discharge-report-weekly",
        "cron": "15 7 * * 1",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/discharge-report",
        "body": {"surety": "ALL", "days_back": 7},
        "slack": "#leads",
        "desc": "Monday discharge/exoneration register (7d)",
    },
    {
        "id": "ops-digest-morning",
        "cron": "30 8 * * *",
        "tz": "America/New_York",
        "method": "POST",
        "path": "/api/automation/ops-digest",
        "body": {"hours_back": 24, "post_slack": True},
        "slack": "#leads",
        "desc": "Combined lead + lifecycle + risk digest to Slack",
    },
]
