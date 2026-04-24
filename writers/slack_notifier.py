"""
Slack Notifier — ShamrockLeads

Sends arrest alerts, scraper health, and lead notifications to Slack.
Supports per-county channels and per-tenant routing.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import requests

from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Sends formatted Slack notifications for arrest events.

    Supports:
    - New arrest alerts (per-county channels)
    - Hot lead alerts (for staff)
    - Scraper health/error reports
    - Ingestion summaries
    """

    def __init__(
        self,
        webhook_arrests: Optional[str] = None,
        webhook_leads: Optional[str] = None,
        webhook_errors: Optional[str] = None,
    ):
        self.webhook_arrests = webhook_arrests or os.getenv("SLACK_WEBHOOK_ARRESTS", "")
        self.webhook_leads = webhook_leads or os.getenv("SLACK_WEBHOOK_LEADS", "")
        self.webhook_errors = webhook_errors or os.getenv("SLACK_WEBHOOK_ERRORS", "")

    def _post(self, webhook_url: str, payload: Dict[str, Any]) -> bool:
        """Send a Slack message. Returns True on success."""
        if not webhook_url:
            logger.warning("Slack webhook URL is empty — skipping notification")
            return False

        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error(f"Slack returned {resp.status_code}: {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")
            return False

    # ── Arrest Alerts ──
    def notify_new_arrests(
        self,
        records: List[ArrestRecord],
        county: str,
        stats: Dict[str, Any],
    ) -> bool:
        """Send a summary of newly scraped arrests."""
        new_count = stats.get("new_records", 0)
        qualified = stats.get("qualified_records", 0)
        dupes = stats.get("duplicates_skipped", 0)
        total = stats.get("total_records", 0)

        if new_count == 0:
            return True  # Nothing to report

        # Build the message
        emoji = "🔥" if qualified > 0 else "📋"
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {county} County — {new_count} New Arrest(s)",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Total Scraped:* {total}"},
                    {"type": "mrkdwn", "text": f"*New:* {new_count}"},
                    {"type": "mrkdwn", "text": f"*Duplicates:* {dupes}"},
                    {"type": "mrkdwn", "text": f"*🔥 Qualified:* {qualified}"},
                ],
            },
        ]

        # Add top-3 highest-bond records
        hot = sorted(
            [r for r in records if r.Lead_Score >= 70],
            key=lambda r: r._parse_bond_numeric(),
            reverse=True,
        )[:3]

        if hot:
            hot_lines = []
            for r in hot:
                bond_val = r._parse_bond_numeric()
                hot_lines.append(
                    f"• *{r.Full_Name}* — ${bond_val:,.0f} — _{r.Charges[:60]}_"
                )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🎯 Top Leads:*\n" + "\n".join(hot_lines),
                },
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_ShamrockLeads • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
                }
            ],
        })

        return self._post(self.webhook_arrests, {"blocks": blocks})

    # ── Hot Lead Alert ──
    def notify_hot_lead(self, record: ArrestRecord) -> bool:
        """Send an individual hot lead alert."""
        bond_val = record._parse_bond_numeric()
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🔥 HOT LEAD — {record.County} County",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Name:* {record.Full_Name}"},
                    {"type": "mrkdwn", "text": f"*Bond:* ${bond_val:,.0f}"},
                    {"type": "mrkdwn", "text": f"*Score:* {record.Lead_Score}"},
                    {"type": "mrkdwn", "text": f"*Status:* {record.Status}"},
                    {"type": "mrkdwn", "text": f"*Charges:* {record.Charges[:120]}"},
                    {"type": "mrkdwn", "text": f"*Booking #:* {record.Booking_Number}"},
                ],
            },
        ]

        return self._post(self.webhook_leads, {"blocks": blocks})

    # ── Scraper Error ──
    def notify_scraper_error(self, county: str, error: str) -> bool:
        """Send a scraper error alert."""
        return self._post(self.webhook_errors, {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚨 Scraper Error — {county}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{error[:2000]}```"},
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
                    }],
                },
            ]
        })

    # ── Health Report ──
    def notify_health_report(self, report: Dict[str, Any]) -> bool:
        """Send a periodic health report."""
        lines = []
        for county, status in report.items():
            emoji = "✅" if status.get("ok") else "❌"
            last_run = status.get("last_run", "never")
            count = status.get("records_today", 0)
            lines.append(f"{emoji} *{county}*: {count} records | Last: {last_run}")

        return self._post(self.webhook_arrests, {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "📊 ShamrockLeads — Health Report"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(lines)},
                },
            ]
        })
