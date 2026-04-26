"""
Hardee County Arrest Scraper — OCV (Online Citizen View) API
Source: Hardee County Sheriff's Office
URL: https://apps.myocv.com/share/a27833873
Method: requests GET — OCV S3 JSON (same vendor as Hendry County)
Fields: Name, InmateID, Booking Date, Race, Gender, Height, Weight, Mugshot, Charges, Bond
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# OCV uses a standard S3 JSON endpoint pattern — app ID a27833873
OCV_APP_ID = "a27833873"
INMATES_JSON_URL = f"https://myocv.s3.amazonaws.com/ocvapps/{OCV_APP_ID}/inmates.json"
DETAIL_BASE_URL = f"https://apps.myocv.com/share/{OCV_APP_ID}"
FACILITY = "Hardee County Jail"
MAX_DETAIL_ENRICHMENT = 30


class HardeeCountyScraper(BaseScraper):
    """Hardee County (FL) — OCV JSON + optional detail enrichment (Wauchula area)"""

    @property
    def county(self) -> str:
        return "Hardee"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
        except ImportError:
            logger.error("requests not installed")
            return []

        records = self._phase1_bulk_json(requests)
        if not records:
            logger.warning("Hardee Phase 1 returned 0 records")
            return []

        logger.info(f"Hardee Phase 1: {len(records)} records from OCV JSON")
        self._phase2_enrich_details(records)
        return records

    def _phase1_bulk_json(self, requests_mod) -> List[ArrestRecord]:
        try:
            resp = requests_mod.get(
                INMATES_JSON_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Hardee JSON fetch failed: {e}")
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
                logger.warning(f"Hardee parse error {entry.get('title', '?')}: {e}")

        return records

    def _parse_json_entry(self, entry: dict) -> Optional[ArrestRecord]:
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
            Booking_Date=booking_date,
            Status="In Custody",
            Facility=FACILITY,
            Mugshot_URL=mugshot_url,
            Charges="",
            Bond_Amount="0",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )
        record._detail_id = detail_id
        return record

    def _phase2_enrich_details(self, records: List[ArrestRecord]) -> None:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.warning("DrissionPage not installed - skipping charge enrichment")
            return

        to_enrich = [r for r in records[:MAX_DETAIL_ENRICHMENT] if getattr(r, '_detail_id', '')]
        if not to_enrich:
            return

        logger.info(f"Hardee Phase 2: enriching {len(to_enrich)} records")

        page = None
        try:
            co = self._get_browser_options()
            page = ChromiumPage(addr_or_opts=co)
            enriched = 0

            for i, record in enumerate(to_enrich):
                try:
                    page.get(record.Detail_URL)
                    time.sleep(2)
                    body_el = page.ele('tag:body', timeout=5)
                    if not body_el:
                        continue
                    text = body_el.text
                    charges, total_bond = self._extract_charges(text)
                    if charges:
                        record.Charges = charges
                    if total_bond > 0:
                        record.Bond_Amount = str(total_bond)
                    enriched += 1
                except Exception as e:
                    logger.debug(f"Hardee enrichment failed for {record.Full_Name}: {e}")
                time.sleep(0.5)

            logger.info(f"Hardee Phase 2 done: enriched {enriched}/{len(to_enrich)}")
        except Exception as e:
            logger.warning(f"Hardee Phase 2 browser error: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

    @staticmethod
    def _extract_charges(text: str) -> tuple:
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
