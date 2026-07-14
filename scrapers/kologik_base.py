"""
Base scraper for Kologik Public Jail Roster (Vue SPA).

API (same origin as jailroster.kologik.com):
  POST /Roster/GetJailRosterPost?agencyOri={ORI}&searchByLetter=ALL&historyMode=N

Agency ORI is derived from the public URL query, e.g.:
  https://jailroster.kologik.com/?_fl0070000  →  FL0070000
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urlparse, parse_qs

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

KOLOGIK_BASE = "https://jailroster.kologik.com"


class KologikBaseScraper(BaseScraper):
    """Subclasses set ``county`` and ``agency_ori`` (or ``portal_url``)."""

    @property
    def county(self) -> str:
        raise NotImplementedError

    @property
    def agency_ori(self) -> str:
        """e.g. 'FL0070000'."""
        portal = getattr(self, "portal_url", None)
        if portal:
            url = portal() if callable(portal) else portal
            return self.ori_from_portal_url(str(url))
        raise NotImplementedError("Define agency_ori or portal_url")

    @staticmethod
    def ori_from_portal_url(url: str) -> str:
        """Parse Kologik URL query into agency ORI.

        ``?_fl0070000`` → ``FL0070000`` (split on ``_``, take last segment).
        """
        parsed = urlparse(url)
        # Query may be bare key like ?_fl0070000
        q = parsed.query or ""
        if q:
            # First key often is the agency token
            key = q.split("&")[0].split("=")[0]
            token = key.split("_")[-1] if "_" in key else key
            if token:
                return token.upper()
        # Path fallback
        m = re.search(r"([A-Z]{2}\d{7})", url, re.I)
        if m:
            return m.group(1).upper()
        raise ValueError(f"Cannot derive Kologik agency ORI from URL: {url}")

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        ori = self.agency_ori
        url = f"{KOLOGIK_BASE}/Roster/GetJailRosterPost"
        params = {
            "agencyOri": ori,
            "searchByLetter": "ALL",
            "historyMode": "N",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{KOLOGIK_BASE}/?_{ori.lower()}",
            "Origin": KOLOGIK_BASE,
        }
        logger.info(f"📥 Kologik roster for {self.county} (ORI={ori})")
        try:
            resp = requests.post(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"{self.county} Kologik fetch failed: {e}")
            return []

        if not isinstance(data, list):
            logger.warning(f"{self.county}: unexpected Kologik payload type {type(data)}")
            return []

        records: List[ArrestRecord] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            first = (row.get("first") or "").strip()
            last = (row.get("last") or "").strip()
            middle = (row.get("middle") or "").strip()
            suffix = (row.get("suffix") or "").strip()
            if not last and not first:
                continue
            full = f"{last}, {first} {middle} {suffix}".strip().replace("  ", " ")
            booking = str(
                row.get("ccn")
                or row.get("arrestid")
                or row.get("nameid")
                or f"KOL_{int(time.time())}"
            )
            # Charges can be nested list
            charges_raw = row.get("charges") or row.get("charge") or row.get("offenses") or []
            charge_parts = []
            total_bond = 0.0
            if isinstance(charges_raw, list):
                for c in charges_raw:
                    if isinstance(c, dict):
                        desc = c.get("charge") or c.get("description") or c.get("offense") or ""
                        if desc:
                            charge_parts.append(str(desc))
                        for bk in ("bond", "bond_amount", "bondAmount", "bail"):
                            if c.get(bk) not in (None, ""):
                                try:
                                    total_bond += float(re.sub(r"[^\d.]", "", str(c.get(bk))) or 0)
                                except ValueError:
                                    pass
                    else:
                        charge_parts.append(str(c))
            elif charges_raw:
                charge_parts.append(str(charges_raw))

            if not charge_parts:
                # Flat fields
                for k in ("offense", "offensedesc", "charge_desc"):
                    if row.get(k):
                        charge_parts.append(str(row[k]))

            bond_str = str(row.get("bond") or row.get("total_bond") or total_bond or "0")
            bond = re.sub(r"[^\d.]", "", bond_str) or "0"

            incarcerated = str(row.get("incarcerated") or "").upper()
            rel = str(row.get("rel_date_time") or "").strip()
            if rel or incarcerated in ("R", "RELEASED", "N"):
                status = "Released"
            else:
                status = "In Custody"

            records.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state or "FL",
                    Full_Name=full,
                    First_Name=first,
                    Last_Name=last,
                    Middle_Name=middle,
                    Booking_Number=booking,
                    Booking_Date=str(row.get("book_date_time") or row.get("arr_date_time") or ""),
                    Arrest_Date=str(row.get("arr_date_time") or ""),
                    Release_Date=rel,
                    DOB=str(row.get("dob") or ""),
                    Race=str(row.get("race") or ""),
                    Sex=str(row.get("sex") or ""),
                    Height=str(row.get("height") or ""),
                    Weight=str(row.get("weight") or ""),
                    Charges=" | ".join(charge_parts) if charge_parts else "Unknown",
                    Bond_Amount=bond,
                    Status=status,
                    Agency=str(row.get("arresting_agency") or ""),
                    Detail_URL=f"{KOLOGIK_BASE}/?_{ori.lower()}",
                )
            )

        logger.info(
            f"✅ {self.county}: Kologik {len(records)} records in {time.time() - start:.1f}s"
        )
        return records
