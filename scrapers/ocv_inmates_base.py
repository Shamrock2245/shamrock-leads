"""
OCV (myocv) public inmates.json base scraper.

Many NC sheriff sites (Chatham, Stanly, …) publish a live roster at::

    https://myocv.s3.amazonaws.com/ocvapps/{app_id}/inmates.json

Each entry includes name, inmateID, booked date, demographics (in HTML
``content``), and custody status. Charge/bond arrays are often header-only
on the public feed — we still ingest identity + booking for lead flow.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


class OCVInmatesBaseScraper(BaseScraper):
    """Subclasses set ``county``, ``state``, ``app_id``, optional portal_url."""

    @property
    def county(self) -> str:
        raise NotImplementedError

    @property
    def state(self) -> str:
        return "NC"

    @property
    def app_id(self) -> str:
        """OCV app id, e.g. ``a104027312``."""
        raise NotImplementedError

    @property
    def portal_url(self) -> str:
        return ""

    @property
    def inmates_json_url(self) -> str:
        return f"https://myocv.s3.amazonaws.com/ocvapps/{self.app_id}/inmates.json"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)

        try:
            resp = session.get(self.inmates_json_url, timeout=45)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("%s OCV inmates.json failed: %s", self.county, e)
            return []

        if not isinstance(data, list):
            logger.warning("%s OCV: unexpected JSON type %s", self.county, type(data))
            return []

        records: List[ArrestRecord] = []
        for item in data:
            try:
                rec = self._item_to_record(item)
                if rec and rec.Booking_Number:
                    records.append(rec)
            except Exception as e:
                logger.debug("%s OCV item parse fail: %s", self.county, e)

        logger.info(
            "%s OCV: %d inmates from %s in %.1fs",
            self.county,
            len(records),
            self.app_id,
            time.time() - start,
        )
        return records

    def _item_to_record(self, item: dict) -> Optional[ArrestRecord]:
        last = (item.get("lastName") or "").strip()
        first = (item.get("firstName") or "").strip()
        title = (item.get("title") or "").strip()
        if title and "," in title:
            full = title
            if not last:
                last = title.split(",", 1)[0].strip()
            if not first:
                first = title.split(",", 1)[1].strip().split()[0] if "," in title else ""
        else:
            full = (item.get("titleWithFirst") or f"{last}, {first}").strip(", ")

        if not full or len(full) < 2:
            return None

        inmate_id = str(item.get("inmateID") or "").strip()
        oid = item.get("_id")
        if isinstance(oid, dict):
            oid = oid.get("$id") or ""
        booking = inmate_id or str(oid or "")
        if not booking:
            return None

        demo = self._parse_content(item.get("content") or "")
        booked = demo.get("booked") or self._ts_to_date(item.get("date"))
        custody = (item.get("custody_status_cd") or "").upper()
        status = "In Custody" if custody in ("IN", "I", "ACTIVE", "") else custody or "In Custody"

        # chargeArray is often just column headers on public feed
        charges = "Unknown"
        bond = "0"
        ca = item.get("chargeArray")
        if isinstance(ca, list) and ca and isinstance(ca[0], dict):
            descs = []
            total = 0.0
            for ch in ca:
                d = ch.get("chargeDescription") or ch.get("description") or ""
                if d:
                    descs.append(str(d))
                b = ch.get("bondAmount") or ch.get("bond") or 0
                try:
                    total += float(re.sub(r"[^\d.]", "", str(b)) or 0)
                except ValueError:
                    pass
            if descs:
                charges = " | ".join(descs)
            if total > 0:
                bond = f"{total:.2f}"

        mug = ""
        images = item.get("images") or []
        if images and isinstance(images[0], dict):
            mug = images[0].get("large") or images[0].get("small") or ""
            if "missing-image" in mug:
                mug = ""

        detail = self.portal_url or self.inmates_json_url
        if self.portal_url and oid:
            # OCV detail path pattern used on sheriff sites
            base = self.portal_url.rstrip("/")
            if "inmate" in base.lower():
                detail = f"{base}/{oid}"
            else:
                detail = f"{base}/inmateList/{oid}"

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=full if not full.isupper() else full.title(),
            First_Name=first.title() if first.isupper() else first,
            Last_Name=last.title() if last.isupper() else last,
            Booking_Number=str(booking),
            Person_ID=str(inmate_id or booking),
            Booking_Date=booked,
            Sex=(demo.get("gender") or "")[:1].upper(),
            Race=demo.get("race") or "",
            Age_At_Arrest=str(demo.get("age") or ""),
            Height=demo.get("height") or "",
            Weight=demo.get("weight") or "",
            Charges=charges,
            Bond_Amount=bond,
            Status=status,
            Facility=f"{self.county} County Detention",
            Agency=item.get("agencyID") or f"{self.county} County Sheriff",
            Mugshot_URL=mug,
            Detail_URL=detail,
        )

    @staticmethod
    def _parse_content(html: str) -> dict:
        out: dict = {}
        if not html:
            return out
        text = BeautifulSoup(html, "html.parser").get_text("\n")
        patterns = {
            "height": r"Height:\s*(.+)",
            "weight": r"Weight:\s*(.+)",
            "gender": r"Gender:\s*([A-Za-z])",
            "race": r"Race:\s*([A-Za-z]+)",
            "age": r"Age:\s*(\d+)",
            "booked": r"Booked Date:\s*(.+)",
            "inmate_id": r"Inmate ID:\s*(\S+)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                val = m.group(1).strip()
                if "unavailable" in val.lower() or val == "Currently Unavailable":
                    continue
                # strip timezone suffix noise
                if key == "booked":
                    val = re.sub(r"\s+[A-Z]{2,4}$", "", val).strip()
                out[key] = val
        return out

    @staticmethod
    def _ts_to_date(date_obj) -> str:
        if not date_obj:
            return ""
        try:
            if isinstance(date_obj, dict) and "sec" in date_obj:
                dt = datetime.fromtimestamp(int(date_obj["sec"]), tz=timezone.utc)
                return dt.strftime("%m/%d/%Y %H:%M")
        except Exception:
            pass
        return ""
