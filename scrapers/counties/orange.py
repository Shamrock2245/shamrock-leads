"""
Orange County Arrest Scraper — BestJail JSON API.

Source: Orange County Corrections Division (OCFL)
URL: https://netapps.ocfl.net/BestJail/Home/Inmates
Method: Pure HTTP — JSON API endpoints (no browser needed)

API Endpoints:
  GET /BestJail/Home/getInmates/{letter} → [{bookingNumber, inmateName}, ...]
  GET /BestJail/Home/getInmateDetails/{bookingNumber} → [{NAME, RACE, GENDER, BIRTH, DATEBOOKED, ...}]
  GET /BestJail/Home/getCharges/{bookingNumber} → [{Charge, BondAmount, ArrestingAgency, ...}]

Architecture:
1. Iterate A-Z to get full inmate list (booking numbers + names)
2. Filter to current-year bookings (26XXXXXX for 2026) to skip old inmates
3. For recent bookings, fetch details + charges via API
4. Filter to last N days by booking date
5. Map all fields → ArrestRecord schema

Note: The old ASP portal at apps.ocfl.net/bailbond now 302-redirects to BestJail.
"""

import logging
import re
import string
import time
import requests
from datetime import datetime, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://netapps.ocfl.net/BestJail/Home"
INMATES_URL = f"{BASE_URL}/getInmates"
DETAILS_URL = f"{BASE_URL}/getInmateDetails"
CHARGES_URL = f"{BASE_URL}/getCharges"

# Rate limiting
DETAIL_DELAY_S = 0.15  # Delay between detail requests
LETTER_DELAY_S = 0.1   # Delay between letter searches
REQUEST_TIMEOUT = 15

# Only enrich bookings from current year prefix (e.g. "26" for 2026)
CURRENT_YEAR_PREFIX = str(datetime.now().year)[-2:]  # "26"
DAYS_BACK = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Inmates",
}

FACILITY = "Orange County Jail"


class OrangeCountyScraper(BaseScraper):
    """Orange County (FL) arrest scraper — BestJail JSON API (pure HTTP)."""

    @property
    def county(self) -> str:
        return "Orange"

    @property
    def roster_url(self) -> Optional[str]:
        return f"{BASE_URL}/Inmates"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Orange County via BestJail JSON API endpoints."""
        session = requests.Session()
        session.headers.update(HEADERS)

        # Step 1: Collect booking numbers by iterating A-Z
        all_inmates = self._fetch_all_inmates(session)
        if not all_inmates:
            logger.warning("⚠️ Orange: No inmates found in search")
            return []

        # Step 2: Pre-filter to current-year bookings only (26XXXXXX)
        recent_inmates = [
            i for i in all_inmates
            if i.get("bookingNumber", "").startswith(CURRENT_YEAR_PREFIX)
        ]
        logger.info(
            f"📋 Orange: {len(all_inmates)} total inmates, "
            f"{len(recent_inmates)} from 20{CURRENT_YEAR_PREFIX}"
        )

        # Step 3: Fetch details + charges for recent bookings
        records = []
        enriched = 0
        skipped_old = 0
        errors = 0

        for inmate in recent_inmates:
            booking_num = inmate.get("bookingNumber", "").strip()
            if not booking_num:
                continue

            # Fetch detail (contains booking date)
            detail = self._fetch_detail(session, booking_num)
            if not detail:
                errors += 1
                continue

            # Parse booking date and filter to last N days
            booking_date = self._parse_booking_date(detail)
            if booking_date:
                age_days = (datetime.now(timezone.utc) - booking_date).days
                if age_days > DAYS_BACK:
                    skipped_old += 1
                    continue

            # Fetch charges
            charges = self._fetch_charges(session, booking_num)

            # Build record
            record = self._build_record(detail, charges, booking_num)
            if record:
                records.append(record)
                enriched += 1

            if enriched % 25 == 0 and enriched > 0:
                logger.info(f"  📊 Orange: Enriched {enriched} records...")

            time.sleep(DETAIL_DELAY_S)

        logger.info(
            f"✅ Orange: {enriched} recent records "
            f"(skipped {skipped_old} older, {errors} errors)"
        )
        return records

    def _fetch_all_inmates(self, session: requests.Session) -> list:
        """Fetch all inmates by searching each letter A-Z."""
        all_inmates = []
        seen_bookings = set()

        for letter in string.ascii_lowercase:
            try:
                resp = session.get(
                    f"{INMATES_URL}/{letter}",
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code != 200:
                    logger.warning(f"⚠️ Orange: Letter '{letter}' returned {resp.status_code}")
                    continue

                data = resp.json()
                if not isinstance(data, list):
                    continue

                for inmate in data:
                    bn = inmate.get("bookingNumber", "").strip()
                    if bn and bn not in seen_bookings:
                        seen_bookings.add(bn)
                        all_inmates.append(inmate)

                time.sleep(LETTER_DELAY_S)

            except Exception as e:
                logger.warning(f"⚠️ Orange: Error on letter '{letter}': {e}")
                continue

        return all_inmates

    def _fetch_detail(self, session: requests.Session, booking_num: str) -> Optional[dict]:
        """Fetch inmate detail by booking number."""
        try:
            resp = session.get(
                f"{DETAILS_URL}/{booking_num}",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return None

        except Exception as e:
            logger.debug(f"Detail fetch failed for {booking_num}: {e}")
            return None

    def _fetch_charges(self, session: requests.Session, booking_num: str) -> list:
        """Fetch charges for a booking number."""
        try:
            resp = session.get(
                f"{CHARGES_URL}/{booking_num}",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            return data if isinstance(data, list) else []

        except Exception as e:
            logger.debug(f"Charges fetch failed for {booking_num}: {e}")
            return []

    def _parse_booking_date(self, detail: dict) -> Optional[datetime]:
        """Parse booking date from detail response."""
        date_str = detail.get("DATEBOOKED", "").strip()
        time_str = detail.get("TIMEBOOKED", "").strip()

        if not date_str:
            return None

        try:
            if time_str:
                dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M%p")
            else:
                dt = datetime.strptime(date_str, "%m/%d/%Y")
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    def _build_record(
        self, detail: dict, charges: list, booking_num: str
    ) -> Optional[ArrestRecord]:
        """Build an ArrestRecord from API responses."""
        try:
            name = detail.get("NAME", "").strip()
            if not name:
                return None

            # Parse name (format: "LASTNAME, FIRSTNAME MIDDLE")
            first_name, last_name = "", ""
            if "," in name:
                parts = name.split(",", 1)
                last_name = parts[0].strip().title()
                first_parts = parts[1].strip().split()
                first_name = first_parts[0].title() if first_parts else ""
            else:
                first_name = name.title()

            # Parse charges and bond
            charge_list = []
            total_bond = 0.0
            arresting_agency = ""

            for charge in charges:
                charge_text = charge.get("Charge", "").strip()
                if charge_text:
                    if "-" in charge_text:
                        parts = charge_text.split("-", 1)
                        charge_text = parts[-1].strip() if len(parts) > 1 else charge_text
                    charge_list.append(charge_text)

                try:
                    bond_str = charge.get("BondAmount", "0").strip()
                    bond_val = float(re.sub(r"[,$]", "", bond_str))
                    total_bond += bond_val
                except (ValueError, TypeError):
                    pass

                if not arresting_agency:
                    arresting_agency = charge.get("ArrestingAgency", "").strip()

            # Build address
            street = detail.get("STREET", "").strip()
            apt = detail.get("APTNUM", "").strip()
            city = detail.get("CITY", "").strip()
            state = detail.get("STATE", "").strip()
            zipcode = detail.get("ZIPCODE", "").strip()

            address_parts = [street]
            if apt:
                address_parts.append(f"Apt {apt}")
            if city:
                address_parts.append(city)
            if state:
                address_parts.append(state)
            if zipcode:
                address_parts.append(zipcode)
            full_address = ", ".join(p for p in address_parts if p)

            booking_date = self._parse_booking_date(detail)
            booking_date_str = booking_date.strftime("%m/%d/%Y %I:%M %p") if booking_date else ""

            age = detail.get("BIRTH", "").strip()

            record = ArrestRecord(
                County="Orange",
                Booking_Number=booking_num,
                First_Name=first_name,
                Last_Name=last_name,
                Full_Name=f"{first_name} {last_name}".strip(),
                Booking_Date=booking_date_str,
                Booking_Date_Parsed=booking_date,
                Charges="; ".join(charge_list) if charge_list else "Not Available",
                Bond_Amount=f"${total_bond:,.2f}" if total_bond > 0 else "$0.00",
                Race=detail.get("RACE", "").strip().title(),
                Gender=detail.get("GENDER", "").strip().title(),
                Age=age,
                Address=full_address,
                City=city.title() if city else "",
                State=state,
                Zip_Code=zipcode,
                Arresting_Agency=arresting_agency.title() if arresting_agency else "Orange County Sheriff Office",
                Facility=FACILITY,
                Status="In Custody" if not charges else charges[0].get("CaseStatus", "In Custody"),
                scraped_at=datetime.now(timezone.utc),
            )

            return record

        except Exception as e:
            logger.warning(f"⚠️ Orange: Failed to build record for {booking_num}: {e}")
            return None
