"""
Hendry County Arrest Scraper — BustedNewspaper RSS Feed
=======================================================
Source: BustedNewspaper.com (aggregates from Hendry County Sheriff's Office)
URL: https://bustednewspaper.com/mugshots/florida/hendry-county/feed/
Method: HTTP GET → RSS/XML parse → ArrestRecord

Extracts from BustedNewspaper's hourly-updated RSS feed:
  - Full name (LAST, FIRST MIDDLE format)
  - Age, height, weight, race, sex
  - Booking date
  - Charge descriptions with Florida statute numbers
  - Bond amounts per charge
  - Mugshot URLs (CDN-hosted)
  - Detail page URL for each booking

NO CAPTCHA required — simple HTTP GET to a WordPress RSS feed.
NO headless browser needed.
NO anti-bot evasion required.

HISTORY:
  - v1 (original): OCV S3 JSON + curl_cffi detail page enrichment
    → Phase 1 (demographics) worked, Phase 2 (charges/bonds) unreliable
  - v2: JailTracker rewrite — Blazor WASM with CAPTCHA solving
    → Server-side 400 errors, session bugs, pagination broken
  - v3 (current): BustedNewspaper RSS feed — dead simple, reliable
    → Gets name, charges, bonds, mugshots from clean XML
    → Updated hourly by BustedNewspaper's own JailTracker scraper
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import List, Optional, Tuple

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# RSS feed URL — updated hourly per <sy:updatePeriod>
RSS_FEED_URL = "https://bustednewspaper.com/mugshots/florida/hendry-county/feed/"

# RSS namespaces
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class HendryCountyScraper(BaseScraper):
    """
    Hendry County scraper using BustedNewspaper RSS feed.

    BustedNewspaper aggregates arrest data from the Hendry County
    Sheriff's Office JailTracker system and publishes it as a
    WordPress RSS feed — complete with structured HTML tables
    containing booking details and charges.

    The RSS feed updates hourly and contains the ~20 most recent
    bookings with full details inline in <content:encoded>.
    """

    @property
    def county(self) -> str:
        return "Hendry"

    def scrape(self) -> List[ArrestRecord]:
        """
        Fetch the BustedNewspaper RSS feed and parse each <item>
        into an ArrestRecord.

        Returns:
            List of ArrestRecord instances from the RSS feed.
        """
        logger.info(f"📡 {self.county}: Fetching BustedNewspaper RSS feed...")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        resp = requests.get(RSS_FEED_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        # Parse RSS XML
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            logger.warning(f"⚠️ {self.county}: No <channel> in RSS feed")
            return []

        items = channel.findall("item")
        logger.info(f"📋 {self.county}: Found {len(items)} items in RSS feed")

        records: List[ArrestRecord] = []
        seen_guids: set = set()

        for item in items:
            try:
                record = self._parse_item(item)
                if record is None:
                    continue

                # Deduplicate within this batch by GUID/booking key
                dedup_key = record.get_dedup_key()
                if dedup_key in seen_guids:
                    continue
                seen_guids.add(dedup_key)

                records.append(record)
            except Exception as e:
                title = item.findtext("title", "unknown")
                logger.warning(
                    f"⚠️ {self.county}: Failed to parse item '{title}': {e}"
                )

        logger.info(
            f"✅ {self.county}: Parsed {len(records)} unique records "
            f"from {len(items)} RSS items"
        )
        return records

    def _parse_item(self, item: ET.Element) -> Optional[ArrestRecord]:
        """
        Parse a single RSS <item> into an ArrestRecord.

        The <content:encoded> contains HTML with structured tables:
        - Booking Details table: name, age, height, hair, eye, weight,
          race, sex, booked
        - Charges tables (one per charge): charge description,
          jurisdiction, bond details, bond amount
        """
        # Get basic fields from RSS
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        guid = item.findtext("guid", link)

        # Get the full HTML content
        content_encoded = item.findtext("content:encoded", "", NS)
        if not content_encoded:
            return None

        # Unescape HTML entities
        html = unescape(content_encoded)

        # Parse booking details from the HTML table
        booking = self._extract_table_data(html)

        # Parse name — RSS format is "LAST, FIRST MIDDLE"
        raw_name = booking.get("name", "")
        if not raw_name:
            # Fallback: extract from title ("Agosto, Christal Mugshot | ...")
            name_match = re.match(r"^(.+?)\s+Mugshot\s*\|", title)
            if name_match:
                raw_name = name_match.group(1).upper()

        if not raw_name:
            return None

        first_name, middle_name, last_name = self._parse_name(raw_name)
        full_name = raw_name.strip()

        # Parse booking date/time from pubDate or table
        booking_date = booking.get("booked", "")
        booking_time = ""
        if pub_date:
            try:
                # RFC 2822: "Thu, 11 Jun 2026 09:17:16 +0000"
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                if not booking_date:
                    booking_date = dt.strftime("%Y-%m-%d")
                booking_time = dt.strftime("%H:%M:%S")
            except Exception:
                pass

        # Parse charges and bond from the charge tables
        charges_list, total_bond = self._extract_charges(html)
        charges_str = " | ".join(charges_list) if charges_list else ""

        # Parse age
        age_str = booking.get("age", "")
        age_clean = re.sub(r"\s*years?\s*old\s*", "", age_str, flags=re.I).strip()

        # Parse height — "5 ft 09in(s)" → "509"
        height_raw = booking.get("height", "")
        height = self._normalize_height(height_raw)

        # Parse weight — "160 lbs" → "160"
        weight_raw = booking.get("weight", "")
        weight = re.sub(r"\s*lbs?\s*", "", weight_raw, flags=re.I).strip()

        # Parse race code
        race = booking.get("race", "").strip().upper()

        # Parse sex — "Female" → "F", "Male" → "M"
        sex_raw = booking.get("sex", "").strip()
        sex = sex_raw[0].upper() if sex_raw else ""

        # Extract mugshot URL
        mugshot_url = self._extract_mugshot_url(html)
        # Skip the "no mugshot" placeholder
        if mugshot_url and "nomug.jpg" in mugshot_url:
            mugshot_url = ""

        # Generate a stable booking number from the GUID
        # BustedNewspaper doesn't provide actual booking numbers,
        # so we create a deterministic one from the GUID
        booking_number = self._generate_booking_number(guid, full_name, booking_date)

        return ArrestRecord(
            County="Hendry",
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Arrest_Date=booking_date,  # Same as booking for this source
            Age_At_Arrest=age_clean,
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Mugshot_URL=mugshot_url,
            Charges=charges_str,
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Status="In Custody",  # RSS only shows current inmates
            Facility="Hendry County Jail",
            Agency="Hendry County Sheriff's Office",
            Detail_URL=link,
            extra_data={
                "source": "bustednewspaper_rss",
                "rss_guid": guid,
                "individual_charges": charges_list,
            },
        )

    def _extract_table_data(self, html: str) -> dict:
        """
        Extract key-value pairs from the booking details HTML table.

        The table format is:
            <table class="mt">
              <tr><th>name</th><td>AGOSTO, CHRISTAL</td></tr>
              <tr><th>age</th><td>35 years old</td></tr>
              ...
            </table>
        """
        data = {}
        # Match the first table (booking details, not charges)
        table_match = re.search(
            r'<table\s+class="mt">(.*?)</table>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if not table_match:
            return data

        table_html = table_match.group(1)
        rows = re.findall(
            r"<th>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
            table_html,
            re.DOTALL | re.IGNORECASE,
        )
        for key, value in rows:
            # Strip HTML tags from values
            clean_key = re.sub(r"<[^>]+>", "", key).strip().lower()
            clean_val = re.sub(r"<[^>]+>", "", value).strip()
            data[clean_key] = clean_val

        return data

    def _extract_charges(self, html: str) -> Tuple[List[str], float]:
        """
        Extract charges and bond amounts from the charge tables.

        Each charge is in its own <table border="1" class="mt">:
            <tr><th>charge description</th><td>322.34.2A — ...</td></tr>
            <tr><th>jurisdiction</th><td></td></tr>
            <tr><th>bond details</th><td></td></tr>
            <tr><th>bond amount</th><td>150</td></tr>

        Returns:
            (list_of_charge_strings, total_bond_amount)
        """
        charges = []
        total_bond = 0.0

        # Find all tables with border="1" (charge tables)
        charge_tables = re.findall(
            r'<table\s+border="1"[^>]*>(.*?)</table>',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        for table_html in charge_tables:
            rows = re.findall(
                r"<th>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                table_html,
                re.DOTALL | re.IGNORECASE,
            )
            charge_data = {}
            for key, value in rows:
                clean_key = re.sub(r"<[^>]+>", "", key).strip().lower()
                clean_val = re.sub(r"<[^>]+>", "", value).strip()
                # Decode HTML entities like &#8211; (em-dash)
                clean_val = unescape(clean_val)
                charge_data[clean_key] = clean_val

            desc = charge_data.get("charge description", "")
            if desc:
                bond_str = charge_data.get("bond amount", "").strip()
                if bond_str:
                    try:
                        bond_val = float(re.sub(r"[$,]", "", bond_str))
                        total_bond += bond_val
                        charges.append(f"{desc} [Bond: ${bond_val:,.0f}]")
                    except (ValueError, TypeError):
                        charges.append(desc)
                else:
                    charges.append(desc)

        # Also check for the "no charges" message
        if not charges and "Information about charges is not available yet" in html:
            charges = ["Charges pending"]

        return charges, total_bond

    def _extract_mugshot_url(self, html: str) -> str:
        """Extract the mugshot image URL from the HTML content."""
        # Look for the first <img> tag with src
        img_match = re.search(
            r'<img[^>]+src="(https://cdn\.bustednewspaper\.com/[^"]+)"',
            html,
            re.IGNORECASE,
        )
        if img_match:
            return img_match.group(1)
        return ""

    def _parse_name(self, raw_name: str) -> Tuple[str, str, str]:
        """
        Parse "LAST, FIRST MIDDLE" into (first, middle, last).

        Examples:
            "AGOSTO, CHRISTAL" → ("Christal", "", "Agosto")
            "DE SANTIAGO BALDERAS, JUAN ANTONIO" → ("Juan", "Antonio", "De Santiago Balderas")
            "MOLINA-CASTRO, MARVIN LEONARDO" → ("Marvin", "Leonardo", "Molina-Castro")
        """
        if "," in raw_name:
            parts = raw_name.split(",", 1)
            last = parts[0].strip().title()
            rest = parts[1].strip().split()
            first = rest[0].title() if rest else ""
            middle = " ".join(r.title() for r in rest[1:]) if len(rest) > 1 else ""
        else:
            # No comma — treat as "FIRST LAST"
            parts = raw_name.strip().split()
            first = parts[0].title() if parts else ""
            last = parts[-1].title() if len(parts) > 1 else ""
            middle = " ".join(p.title() for p in parts[1:-1]) if len(parts) > 2 else ""

        return first, middle, last

    def _normalize_height(self, height_raw: str) -> str:
        """
        Normalize height from "5 ft 09in(s)" to "509" format.
        """
        match = re.match(r"(\d+)\s*ft\s*(\d+)", height_raw, re.I)
        if match:
            feet = match.group(1)
            inches = match.group(2).zfill(2)
            return f"{feet}{inches}"
        return height_raw.strip()

    def _generate_booking_number(
        self, guid: str, name: str, booking_date: str
    ) -> str:
        """
        Generate a stable, deterministic booking number from the GUID.

        BustedNewspaper doesn't expose actual booking numbers from JailTracker.
        We create a stable hash-based ID that won't change between runs,
        ensuring proper deduplication.

        Format: BN-HENDRY-{date}-{hash8}
        """
        raw = f"{guid}:{name}:{booking_date}"
        hash_hex = hashlib.md5(raw.encode()).hexdigest()[:8].upper()
        date_short = booking_date.replace("-", "") if booking_date else "00000000"
        return f"BN-HENDRY-{date_short}-{hash_hex}"
