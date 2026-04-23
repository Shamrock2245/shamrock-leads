"""
Highlands County Arrest Scraper — OCV/VINE JSON Feed.
Source: Highlands County Sheriff's Office
URL: https://www.highlandssheriff.org/inmateSearch
Method: requests — OCV S3 JSON feed + detail page enrichment
"""
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# OCV S3 JSON feed — Highlands County
INMATES_JSON_URL = "https://myocv.s3.amazonaws.com/ocvapps/a999041447/inmates.json"
DETAIL_BASE_URL = "https://www.highlandssheriff.org/inmateSearch"
FACILITY = "Highlands County Jail"
MAX_DETAIL_ENRICHMENT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


class HighlandsCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Highlands"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update(HEADERS)

        # Phase 1: Fetch OCV JSON
        records = []
        try:
            resp = session.get(INMATES_JSON_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Highlands: {len(data)} inmates from OCV JSON")
            for item in data:
                record = self._parse_ocv_item(item)
                if record:
                    records.append(record)
        except Exception as e:
            logger.warning(f"Highlands: OCV JSON failed ({e}), trying HTML fallback")
            records = self._html_fallback(session)

        logger.info(f"Highlands: {len(records)} records")
        return records

    def _parse_ocv_item(self, item: dict) -> Optional[ArrestRecord]:
        try:
            title = item.get("title", "")
            first = item.get("firstName", "")
            last = item.get("lastName", "")
            full_name = item.get("titleWithFirst", title)
            inmate_id = item.get("inmateID", "")
            custody_status = item.get("custody_status_cd", "IN")

            if custody_status.upper() not in ("IN", "ACTIVE", "CUSTODY"):
                return None

            # Parse content HTML for demographics
            content = item.get("content", "")
            demo = self._parse_content(content)

            booking_date = demo.get("booking_date", "")
            mugshot_url = ""
            images = item.get("images", [])
            if images and isinstance(images, list):
                mugshot_url = images[0].get("large", images[0].get("small", ""))

            detail_id = item.get("_id", {}).get("$id", "")
            detail_url = f"{DETAIL_BASE_URL}/{detail_id}" if detail_id else DETAIL_BASE_URL

            return ArrestRecord(
                County=self.county,
                Booking_Number=inmate_id,
                Full_Name=full_name,
                First_Name=first,
                Last_Name=last,
                Booking_Date=booking_date,
                Status="In Custody",
                Facility=FACILITY,
                Race=demo.get("race", ""),
                Sex=demo.get("gender", ""),
                Height=demo.get("height", ""),
                Weight=demo.get("weight", ""),
                Address=demo.get("address", ""),
                City=demo.get("city", ""),
                State=demo.get("state", "FL"),
                Zip=demo.get("zip", ""),
                Mugshot_URL=mugshot_url,
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            )
        except Exception as e:
            logger.debug(f"Highlands parse error: {e}")
            return None

    def _html_fallback(self, session) -> List[ArrestRecord]:
        """Fallback: scrape HTML table from the inmate search page."""
        try:
            from bs4 import BeautifulSoup
            resp = session.get(DETAIL_BASE_URL, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            records = []
            for table in soup.find_all("table"):
                text = table.get_text(" ").lower()
                if any(kw in text for kw in ["name", "booking", "inmate"]):
                    for row in table.find_all("tr")[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 2:
                            continue
                        texts = [c.get_text(strip=True) for c in cells]
                        full_name = texts[0] if texts else ""
                        if not full_name:
                            continue
                        f, m, l = self._pn(full_name)
                        records.append(ArrestRecord(
                            County=self.county,
                            Full_Name=full_name,
                            First_Name=f,
                            Middle_Name=m,
                            Last_Name=l,
                            Booking_Number=texts[1] if len(texts) > 1 else "",
                            Booking_Date=texts[2] if len(texts) > 2 else "",
                            Status="In Custody",
                            Facility=FACILITY,
                            LastCheckedMode="INITIAL",
                        ))
                    break
            return records
        except Exception as e:
            logger.error(f"Highlands HTML fallback: {e}")
            return []

    @staticmethod
    def _parse_content(html: str) -> dict:
        if not html:
            return {}
        text = re.sub(r"<[^>]+>", "\n", html)
        text = re.sub(r"\n+", "\n", text).strip()
        result = {}
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
        m = re.search(r"Booked Date:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", text)
        if m:
            result["booking_date"] = m.group(1)
        return result

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), p[-1] if len(p) >= 2 else ""
