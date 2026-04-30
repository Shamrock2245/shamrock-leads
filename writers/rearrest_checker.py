"""
ShamrockLeads — Re-Arrest Checker (Synchronous)
================================================
Runs inside the scraper process (same container as MongoWriter) to detect
repeat offenders immediately after new arrest records are written.

Queries both `active_bonds` and `bonds` collections for prior bond matches.
Writes results to `rearrest_notifications` for dashboard consumption.

Does NOT auto-send messages. Staff review alerts on the dashboard and
trigger outreach manually (Human-in-the-Loop per Prime Directive #6).

Design mirrors GAS HistoricalBondMonitor.js fuzzy matching logic:
  - Exact last-name match (case-insensitive)
  - First-name prefix match (≥3 chars)
  - Dedup key: defendant_name + booking_number
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection

from config.settings import settings

logger = logging.getLogger(__name__)

# Minimum first-name prefix length for fuzzy match (mirrors GAS HBM_CONFIG)
FIRST_NAME_PREFIX_LEN = 3


def _normalize_name(name: str) -> str:
    """Strip and uppercase a name, collapse whitespace."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", str(name).strip().upper())


def _parse_name_parts(full_name: str) -> tuple:
    """Extract (first_name, last_name) from various formats.

    Handles:
      - "LAST, FIRST MIDDLE"  (jail roster format)
      - "FIRST LAST"          (natural format)
      - "FIRST MIDDLE LAST"   (3+ parts, last is surname)
    """
    name = _normalize_name(full_name)
    if not name:
        return ("", "")

    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        last_name = parts[0]
        first_name = parts[1].split()[0] if parts[1] else ""
    else:
        parts = name.split()
        if len(parts) == 1:
            return (parts[0], "")
        first_name = parts[0]
        last_name = parts[-1]

    return (first_name, last_name)


def _fuzzy_first_name_match(arrest_first: str, bond_first: str) -> bool:
    """Fuzzy first-name match using prefix comparison.

    Mirrors GAS fuzzyFirstNameMatch_() logic:
    - If either name is shorter than FIRST_NAME_PREFIX_LEN, require exact match
    - Otherwise, check if the shorter prefix matches the longer name's start
    """
    a = _normalize_name(arrest_first)
    b = _normalize_name(bond_first)

    if not a or not b:
        return False

    # Exact match
    if a == b:
        return True

    # Prefix match (use shorter name as prefix)
    min_len = min(len(a), len(b))
    if min_len < FIRST_NAME_PREFIX_LEN:
        return a == b  # Require exact for very short names

    prefix_len = max(FIRST_NAME_PREFIX_LEN, min_len)
    return a[:prefix_len] == b[:prefix_len]


class RearrestChecker:
    """Synchronous repeat offender checker for the scraper pipeline.

    Queries MongoDB for prior bonds matching newly-scraped arrest records.
    Writes match notifications to `rearrest_notifications` collection.
    """

    def __init__(self, uri: str = None, db_name: str = None):
        self.uri = uri or settings.MONGODB_URI
        self.db_name = db_name or settings.MONGODB_DB_NAME

        if not self.uri:
            raise ValueError("MONGODB_URI required for RearrestChecker")

        self.client = MongoClient(self.uri)
        self.db = self.client[self.db_name]

        # Collections
        self.active_bonds: Collection = self.db["active_bonds"]
        self.bonds: Collection = self.db["bonds"]
        self.notifications: Collection = self.db["rearrest_notifications"]

        # Ensure dedup index on notifications
        self.notifications.create_index(
            [("defendant_name_norm", 1), ("booking_number", 1)],
            unique=True,
            name="dedup_rearrest_notification",
            sparse=True,
        )

    def check_batch(
        self, records: list, county: str
    ) -> Dict[str, Any]:
        """Check a batch of arrest records for prior bond matches.

        Args:
            records: List of ArrestRecord objects from the scraper
            county: County name

        Returns:
            {"matches_found": int, "notifications_created": int, "skipped_dupes": int}
        """
        matches_found = 0
        notifications_created = 0
        skipped_dupes = 0

        for record in records:
            try:
                result = self._check_single(record, county)
                if result == "match":
                    matches_found += 1
                    notifications_created += 1
                elif result == "dupe":
                    skipped_dupes += 1
            except Exception as e:
                logger.debug(f"Rearrest check error for {getattr(record, 'Full_Name', '?')}: {e}")

        if matches_found > 0:
            logger.info(
                f"🚨 RearrestChecker: {county} — "
                f"{matches_found} match(es), {skipped_dupes} dupes skipped"
            )

        return {
            "matches_found": matches_found,
            "notifications_created": notifications_created,
            "skipped_dupes": skipped_dupes,
        }

    def _check_single(self, record, county: str) -> Optional[str]:
        """Check a single arrest record against bond history.

        Returns:
            "match" — new match found and notification created
            "dupe"  — match exists but already notified
            None    — no match
        """
        full_name = getattr(record, "Full_Name", "") or ""
        booking_number = getattr(record, "Booking_Number", "") or ""
        if not full_name or not booking_number:
            return None

        norm_name = _normalize_name(full_name)
        first_name, last_name = _parse_name_parts(full_name)

        if not last_name:
            return None

        # ── Dedup check: already notified for this arrest? ──────────────────
        existing = self.notifications.find_one({
            "defendant_name_norm": norm_name,
            "booking_number": booking_number,
        })
        if existing:
            return "dupe"

        # ── Search active_bonds collection ──────────────────────────────────
        prior_bonds = self._search_active_bonds(first_name, last_name)

        # ── Search bonds collection (historical/GAS) ────────────────────────
        prior_bonds.extend(self._search_bonds_collection(first_name, last_name))

        if not prior_bonds:
            return None

        # ── Build notification document ─────────────────────────────────────
        best_bond = prior_bonds[0]  # Most recent match
        indemnitor_name = (
            best_bond.get("indemnitor_name")
            or (best_bond.get("indemnitor") or {}).get("name", "")
            or (best_bond.get("indemnitor") or {}).get("firstName", "")
        )
        indemnitor_phone = (
            best_bond.get("indemnitor_phone")
            or (best_bond.get("indemnitor") or {}).get("phone", "")
        )
        indemnitor_email = (
            best_bond.get("indemnitor_email")
            or (best_bond.get("indemnitor") or {}).get("email", "")
        )

        bond_amount_raw = getattr(record, "Bond_Amount", "") or ""
        try:
            bond_amount = float(str(bond_amount_raw).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            bond_amount = 0.0

        notification = {
            # New arrest details
            "defendant_name": full_name,
            "defendant_name_norm": norm_name,
            "booking_number": booking_number,
            "county": county,
            "charges": getattr(record, "Charges", "") or "",
            "bond_amount": bond_amount,
            "arrest_date": getattr(record, "Arrest_Date", "") or "",
            "custody_status": getattr(record, "Custody_Status", "") or "",
            # Prior bond / indemnitor details
            "indemnitor_name": indemnitor_name,
            "indemnitor_phone": indemnitor_phone,
            "indemnitor_email": indemnitor_email,
            "prior_booking_number": best_bond.get("booking_number", ""),
            "prior_bond_amount": best_bond.get("bond_amount", 0),
            "prior_bond_date": (
                best_bond.get("created_at", best_bond.get("bond_date", ""))
            ),
            "prior_defendant_name": best_bond.get("defendant_name", ""),
            "prior_county": best_bond.get("county", ""),
            "prior_bonds_count": len(prior_bonds),
            "prior_bonds_source": best_bond.get("_source", "unknown"),
            # Workflow state
            "status": "pending_review",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "reviewed_by": None,
            "reviewed_at": None,
            "contacted_at": None,
        }

        try:
            self.notifications.insert_one(notification)
            logger.info(
                f"🚨 REPEAT OFFENDER: {full_name} ({county}) — "
                f"prior indemnitor: {indemnitor_name or 'unknown'} "
                f"({indemnitor_phone[-4:] if indemnitor_phone else 'no phone'})"
            )

            # ── Slack alert for high-value matches ──────────────────────────
            self._slack_alert(notification)

            return "match"

        except Exception as e:
            if "duplicate key" in str(e).lower():
                return "dupe"
            raise

    def _search_active_bonds(self, first_name: str, last_name: str) -> List[Dict]:
        """Search active_bonds by last name, then fuzzy-match first name."""
        matches = []

        # Case-insensitive last name query
        cursor = self.active_bonds.find(
            {"defendant_name": {"$regex": last_name, "$options": "i"}},
            {"_id": 0},
        ).sort("created_at", DESCENDING).limit(20)

        for bond in cursor:
            bond_name = bond.get("defendant_name", "")
            bond_first, bond_last = _parse_name_parts(bond_name)

            if _normalize_name(bond_last) != last_name:
                continue

            if _fuzzy_first_name_match(first_name, bond_first):
                bond["_source"] = "active_bonds"
                matches.append(bond)

        return matches

    def _search_bonds_collection(self, first_name: str, last_name: str) -> List[Dict]:
        """Search bonds collection by last name, then fuzzy-match first name."""
        matches = []

        cursor = self.bonds.find(
            {"defendant_name": {"$regex": last_name, "$options": "i"}},
            {"_id": 0},
        ).sort("created_at", DESCENDING).limit(20)

        for bond in cursor:
            bond_name = bond.get("defendant_name", "")
            bond_first, bond_last = _parse_name_parts(bond_name)

            if _normalize_name(bond_last) != last_name:
                continue

            if _fuzzy_first_name_match(first_name, bond_first):
                bond["_source"] = "bonds"
                matches.append(bond)

        return matches

    def _slack_alert(self, notification: Dict):
        """Send Slack alert for high-value repeat offender matches."""
        webhook_url = settings.SLACK_WEBHOOK_LEADS
        if not webhook_url:
            return

        bond_amount = notification.get("bond_amount", 0)
        # Only alert on bonds worth pursuing ($1K+)
        if bond_amount < 1000:
            return

        try:
            import json
            import urllib.request

            defendant = notification["defendant_name"]
            county = notification["county"]
            charges = (notification.get("charges") or "")[:150]
            indemnitor = notification.get("indemnitor_name") or "Unknown"
            phone_last4 = (notification.get("indemnitor_phone") or "")[-4:]
            prior_count = notification.get("prior_bonds_count", 1)

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🚨 Repeat Offender Detected"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Defendant:*\n{defendant}"},
                        {"type": "mrkdwn", "text": f"*County:*\n{county}"},
                        {"type": "mrkdwn", "text": f"*Bond Amount:*\n${bond_amount:,.0f}"},
                        {"type": "mrkdwn", "text": f"*Charges:*\n{charges or 'N/A'}"},
                        {"type": "mrkdwn", "text": f"*Prior Indemnitor:*\n{indemnitor}"},
                        {"type": "mrkdwn", "text": f"*Phone:*\n...{phone_last4}" if phone_last4 else "*Phone:*\nN/A"},
                        {"type": "mrkdwn", "text": f"*Prior Bonds:*\n{prior_count}"},
                    ],
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "📋 Review on dashboard → Command Center → Repeat Offender Alerts"},
                    ],
                },
            ]

            payload = json.dumps({"blocks": blocks}).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            logger.debug("Slack rearrest alert sent")

        except Exception as e:
            logger.warning(f"Slack rearrest alert failed: {e}")

    def close(self):
        """Close the MongoDB connection."""
        self.client.close()
