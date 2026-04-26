"""
Hendry County Arrest Scraper — Hybrid: OCV JSON + Detail Page Enrichment.

Source: Hendry County Sheriff's Office
URL: https://www.hendrysheriff.org/inmateSearch
Method: HTTP bulk fetch + DrissionPage detail page enrichment

Architecture (2-phase):
  Phase 1: HTTP GET to OCV S3 JSON -> all inmates with demographics (~0.1s)
  Phase 2: DrissionPage visits detail pages for recent inmates -> charges + bonds

Self-healing: If Phase 2 (browser) fails, Phase 1 data is still returned
with all demographics. Bond amounts default to "0" if enrichment fails.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

INMATES_JSON_URL = "https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json"
DETAIL_BASE_URL = "https://www.hendrysheriff.org/inmateSearch"
FACILITY = "Hendry County Jail"
MAX_DETAIL_ENRICHMENT = 40


class HendryCountyScraper(BaseScraper):
    """Hendry County (FL) arrest scraper - OCV JSON + detail page enrichment."""

    @property
    def county(self) -> str:
        return "Hendry"

    def scrape(self) -> List[ArrestRecord]:
        """2-phase scrape: bulk JSON then selective detail enrichment."""
        try:
            import requests
        except ImportError:
            logger.error("requests not installed")
            return []

        records = self._phase1_bulk_json(requests)
        if not records:
            logger.warning("Phase 1 returned 0 records")
            return []

        logger.info(f"Phase 1 complete: {len(records)} records from OCV JSON")
        self._phase2_enrich_details(records)
        return records

    def _phase1_bulk_json(self, requests_mod) -> List[ArrestRecord]:
        """Fetch all inmates from OCV S3 JSON endpoint."""
        try:
            resp = requests_mod.get(
                INMATES_JSON_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Hendry JSON fetch failed: {e}")
            return []

        if not isinstance(data, list):
            logger.error(f"Unexpected JSON type: {type(data)}")
            return []

        data.sort(key=lambda x: x.get("date", {}).get("sec", 0), reverse=True)

        records = []
        seen = set()

        for entry in data:
            try:
                record = self._parse_json_entry(entry)
                if not record:
                    continue
                if record.Booking_Number in seen:
                    continue
                seen.add(record.Booking_Number)
                records.append(record)
            except Exception as e:
                logger.warning(f"Error parsing {entry.get('title', '?')}: {e}")

        return records

    def _parse_json_entry(self, entry: dict) -> Optional[ArrestRecord]:
        """Parse a single OCV JSON entry into an ArrestRecord."""
        full_name = entry.get("title", "").strip()
        first_name = entry.get("firstName", "").strip()
        last_name = entry.get("lastName", "").strip()

        if not full_name:
            return None

        middle_name = ""
        if not last_name and "," in full_name:
            parts = full_name.split(",", 1)
            last_name = parts[0].strip()
            fp = parts[1].strip().split()
            first_name = fp[0] if fp else ""
            middle_name = " ".join(fp[1:]) if len(fp) > 1 else ""
        else:
            tf = entry.get("titleWithFirst", "").strip().split()
            if len(tf) > 2:
                middle_name = " ".join(tf[1:-1])

        inmate_id = entry.get("inmateID", "")
        if not inmate_id:
            return None

        booking_date = ""
        date_obj = entry.get("date", {})
        if isinstance(date_obj, dict) and "sec" in date_obj:
            try:
                ts = int(date_obj["sec"])
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                booking_date = dt.strftime("%m/%d/%Y %H:%M")
            except (ValueError, TypeError, OSError):
                pass

        mugshot_url = ""
        images = entry.get("images", [])
        if images and isinstance(images, list):
            img = images[0] if images else {}
            if isinstance(img, dict):
                large = img.get("large", "")
                if large and "missing-image" not in large:
                    mugshot_url = large

        demos = self._parse_content_html(entry.get("content", ""))

        if not booking_date and demos.get("booking_date"):
            booking_date = demos["booking_date"]

        detail_id = ""
        id_obj = entry.get("_id", {})
        if isinstance(id_obj, dict):
            detail_id = id_obj.get("$id", "")
        elif isinstance(id_obj, str):
            detail_id = id_obj
        detail_url = f"{DETAIL_BASE_URL}/{detail_id}" if detail_id else DETAIL_BASE_URL

        record = ArrestRecord(
            County=self.county,
            Booking_Number=inmate_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB="",
            Booking_Date=booking_date,
            Status=demos.get("custody_status", "In Custody"),
            Facility=FACILITY,
            Race=demos.get("race", ""),
            Sex=demos.get("gender", ""),
            Height=demos.get("height", ""),
            Weight=demos.get("weight", ""),
            Address=demos.get("address", ""),
            City=demos.get("city", ""),
            State=demos.get("state", "FL"),
            ZIP=demos.get("zip", ""),
            Mugshot_URL=mugshot_url,
            Charges="",
            Bond_Amount="0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )
        record._detail_id = detail_id
        return record

    def _phase2_enrich_details(self, records: List[ArrestRecord]) -> None:
        """Visit detail pages for recent inmates to extract charges and bonds."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.warning("DrissionPage not installed - skipping charge enrichment")
            return

        to_enrich = [r for r in records[:MAX_DETAIL_ENRICHMENT] if getattr(r, '_detail_id', '')]

        if not to_enrich:
            logger.info("No records to enrich")
            return

        logger.info(f"Phase 2: enriching {len(to_enrich)} recent inmates with charges")

        page = None
        try:
            page = self._setup_browser()
            enriched = 0

            for i, record in enumerate(to_enrich):
                try:
                    page.get(record.Detail_URL)
                    time.sleep(2)

                    body_el = page.ele('tag:body', timeout=5)
                    if not body_el:
                        continue

                    text = body_el.text
                    if not text or 'Record Details' not in text:
                        time.sleep(2)
                        text = body_el.text if body_el else ""

                    charges, total_bond = self._extract_charges_from_text(text)

                    if charges:
                        record.Charges = charges
                    if total_bond > 0:
                        record.Bond_Amount = str(total_bond)

                    enriched += 1

                    if (i + 1) % 10 == 0:
                        logger.info(f"Phase 2 progress: {i+1}/{len(to_enrich)}")

                except Exception as e:
                    logger.debug(f"Enrichment failed for {record.Full_Name}: {e}")

                time.sleep(0.5)

            logger.info(f"Phase 2 done: enriched {enriched}/{len(to_enrich)} with charges")

        except Exception as e:
            logger.warning(f"Phase 2 browser error (Phase 1 data preserved): {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

    @staticmethod
    def _extract_charges_from_text(text: str) -> tuple:
        """Extract charge descriptions and bond amounts from detail page text."""
        charges_list = []
        total_bond = 0.0

        charge_descs = re.findall(r"Charge Description:\s*(.+?)(?:\n|Bond)", text)
        bond_amounts = re.findall(r"Bond Amount:\s*\$?([\d,]+\.?\d*)", text)

        for desc in charge_descs:
            clean = desc.strip()
            if clean:
                charges_list.append(clean)

        for amt in bond_amounts:
            try:
                total_bond += float(amt.replace(",", ""))
            except (ValueError, TypeError):
                pass

        return " | ".join(charges_list) if charges_list else "", total_bond

    def _setup_browser(self):
        """Configure and launch DrissionPage browser."""
        from DrissionPage import ChromiumPage
        co = self._get_browser_options()
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _parse_content_html(html: str) -> dict:
        """Parse the HTML content field for demographics."""
        if not html:
            return {}

        text = re.sub(r"<[^>]+>", "\n", html)
        text = re.sub(r"\n+", "\n", text).strip()
        result = {}

        m = re.search(r"Main Address:\s*\n(.+?)(?:\n|$)", text)
        if m:
            addr = m.group(1).strip()
            if addr.upper() not in ("HOMELESS AT THIS TIME", "STILL AT LARGE", ""):
                result["address"] = addr

        m = re.search(
            r"(?:Main Address:.*?\n.+?\n)([A-Z][A-Za-z\s]+),?\s*([A-Z]{2})\s*(\d{5})?",
            text,
        )
        if m:
            city = m.group(1).strip()
            if "Currently Unavailable" not in city:
                result["city"] = city
            result["state"] = m.group(2)
            if m.group(3):
                result["zip"] = m.group(3)

        m = re.search(r"Height:\s*(\d+)\s*ft\s*(\d+)", text)
        if m:
            result["height"] = f"{m.group(1)}'{m.group(2)}\""

        m = re.search(r"Weight:\s*(\d+)\s*lbs?", text)
        if m:
            result["weight"] = f"{m.group(1)} lbs"

        m = re.search(r"Gender:\s*([A-Z])", text)
        if m:
            result["gender"] = m.group(1)

        m = re.search(r"Race:\s*([A-Z]+)", text)
        if m:
            result["race"] = m.group(1)

        m = re.search(r"Custody Status:\s*(\S+)", text)
        if m:
            status = m.group(1).upper()
            result["custody_status"] = "In Custody" if status == "IN" else status

        m = re.search(r"Booked Date:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", text)
        if m:
            result["booking_date"] = m.group(1)

        return result
