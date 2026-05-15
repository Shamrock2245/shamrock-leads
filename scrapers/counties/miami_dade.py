"""
Miami-Dade County Arrest Scraper — ArcGIS Open Data API Pattern.
Source: Miami-Dade County Open Data Hub (ArcGIS REST API)
API: https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/miamidade_jail_data/FeatureServer/0
Features:
- ArcGIS REST API pagination (resultOffset/resultRecordCount)
- Date-based filtering to only fetch recent bookings
- curl_cffi for TLS fingerprint spoofing
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CFFI = False

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
ARCGIS_BASE_URL = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/miamidade_jail_data/FeatureServer/0"
QUERY_ENDPOINT = f"{ARCGIS_BASE_URL}/query"
DAYS_BACK = 3  # Fetch bookings from the last 3 days
PAGE_SIZE = 200
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gis-mdc.opendata.arcgis.com/",
    "Origin": "https://gis-mdc.opendata.arcgis.com",
}

IMPERSONATE = "chrome131"

class MiamiDadeCountyScraper(BaseScraper):
    """Miami-Dade County (FL) arrest scraper — ArcGIS Open Data API."""

    @property
    def county(self) -> str:
        return "Miami-Dade"

    @property
    def roster_url(self) -> str:
        return "https://gis-mdc.opendata.arcgis.com/datasets/jail-bookings-may-29-2015-to-current"

    def scrape(self) -> List[ArrestRecord]:
        """Main scrape pipeline: fetch paginated ArcGIS data → return records."""
        start_time = time.time()
        logger.info(f"[{self.county}] Starting ArcGIS Open Data scrape...")

        # Calculate the cutoff date
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d 00:00:00")
        
        # ArcGIS SQL where clause
        where_clause = f"BookDate >= timestamp '{cutoff_str}'"
        
        all_records = []
        offset = 0
        
        session = cffi_requests.Session(impersonate=IMPERSONATE) if HAS_CFFI else cffi_requests.Session()
        session.headers.update(HEADERS)

        for page in range(MAX_PAGES):
            params = {
                "where": where_clause,
                "outFields": "*",
                "orderByFields": "BookDate DESC, ObjectId DESC",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json"
            }
            
            try:
                logger.debug(f"[{self.county}] Fetching page {page+1} (offset {offset})...")
                resp = session.get(QUERY_ENDPOINT, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                
                if "error" in data:
                    logger.error(f"[{self.county}] ArcGIS API Error: {data['error']}")
                    break
                    
                features = data.get("features", [])
                if not features:
                    logger.info(f"[{self.county}] No more features found at offset {offset}.")
                    break
                    
                for feature in features:
                    attrs = feature.get("attributes", {})
                    record = self._parse_record(attrs)
                    if record:
                        all_records.append(record)
                        
                exceeded = data.get("exceededTransferLimit", False)
                if not exceeded:
                    logger.debug(f"[{self.county}] Reached end of dataset (exceededTransferLimit=False).")
                    break
                    
                offset += PAGE_SIZE
                time.sleep(1.0)  # Be nice to the API
                
            except Exception as e:
                logger.error(f"[{self.county}] Error fetching page {page+1}: {e}")
                break

        logger.info(f"[{self.county}] Scrape complete. Found {len(all_records)} records in {time.time() - start_time:.1f}s.")
        return all_records

    def _parse_record(self, attrs: Dict[str, Any]) -> Optional[ArrestRecord]:
        """Convert an ArcGIS feature attribute dict into an ArrestRecord."""
        try:
            # ArcGIS dates are in milliseconds since epoch
            book_date_ms = attrs.get("BookDate")
            booking_date_str = ""
            if book_date_ms:
                dt = datetime.fromtimestamp(book_date_ms / 1000.0, tz=timezone.utc)
                booking_date_str = dt.strftime("%Y-%m-%d")

            # Name parsing
            full_name = attrs.get("Defendant", "").strip()
            first_name, middle_name, last_name = self._parse_name(full_name)
            
            # DOB
            dob_raw = attrs.get("DOB", "")
            dob_str = ""
            if dob_raw and len(dob_raw) >= 10:
                dob_str = dob_raw[:10]  # Usually YYYY-MM-DD
                
            # Address
            address = (attrs.get("Address") or "").strip()
            city = (attrs.get("City") or "").strip()
            state = (attrs.get("State") or "FL").strip()
            zip_code = (attrs.get("Zip") or "").strip()
            
            # Charges
            charges_list = []
            for i in range(1, 4):
                charge = attrs.get(f"Charge{i}")
                if charge and charge.strip():
                    charges_list.append(charge.strip())
            
            charges_str = " | ".join(charges_list) if charges_list else "UNKNOWN CHARGE"
            
            # Unique ID (ArcGIS GlobalID or ObjectId)
            booking_number = attrs.get("GlobalID") or str(attrs.get("ObjectId", ""))
            if not booking_number:
                return None

            return ArrestRecord(
                County=self.county,
                Booking_Number=booking_number,
                Full_Name=full_name,
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                DOB=dob_str,
                Booking_Date=booking_date_str,
                Address=address,
                City=city,
                State=state,
                ZIP=zip_code,
                Charges=charges_str,
                Status="In Custody",  # Default assumption for recent bookings
                Facility="Miami-Dade Corrections",
                LastCheckedMode="INITIAL"
            )
        except Exception as e:
            logger.warning(f"[{self.county}] Error parsing record {attrs.get('ObjectId')}: {e}")
            return None

    @staticmethod
    def _parse_name(name_str: str) -> tuple[str, str, str]:
        """Parse 'LAST FIRST MIDDLE' or 'LAST, FIRST MIDDLE' into components."""
        if not name_str:
            return "", "", ""
            
        # Handle "LAST, FIRST MIDDLE"
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip().split()
            first_name = first_middle[0] if first_middle else ""
            middle_name = " ".join(first_middle[1:]) if len(first_middle) > 1 else ""
            return first_name, middle_name, last_name
            
        # Handle "LAST FIRST MIDDLE" (common in Miami-Dade data)
        parts = name_str.split()
        if len(parts) == 1:
            return "", "", parts[0]
        elif len(parts) == 2:
            return parts[1], "", parts[0]
        else:
            # Assume first word is last name, second is first name, rest is middle
            return parts[1], " ".join(parts[2:]), parts[0]
