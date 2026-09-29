"""
Sumner County (TN) Arrest Scraper — MyOCV Inmates JSON Feed.

Primary: https://apps.myocv.com/feed/rtjb/a46036101/inmatesV3  (Real-time JSON feed ~670 inmates)
Secondary: S3 dump and HTML pagination fallback.

Official source identifier: Source-issued Inmate ID / BookedNo (numeric).
Contract: Displayed name, Inmate ID, booking timestamp, charges, bond amounts.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# Real-time rtjb feed is primary (contains live timestamps up to today); S3 dumps as secondary
OCV_FEEDS = (
    "https://apps.myocv.com/feed/rtjb/a46036101/inmatesV3",
    "https://myocv.s3.us-east-1.amazonaws.com/ocvapps/a46036101/SumnerInmates.json",
    "https://myocv.s3.amazonaws.com/ocvapps/a46036101/SumnerInmates.json",
)
PORTAL_URL = "https://www.sumnersherifftn.gov/inmates"
FACILITY = "Sumner County Jail"
AGENCY = "Sumner County Sheriff's Office"
MAX_HTML_PAGES = 40
MAX_DETAILS = 40

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}


class SumnerScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Sumner"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records = self._scrape_ocv()
        if not records:
            logger.warning("Sumner: OCV feed empty/failed — HTML page walk fallback")
            records = self._scrape_html_pages()
        logger.info(f"✅ Sumner (TN): {len(records)} records in {time.time() - start:.1f}s")
        return records

    # ── Primary: OCV JSON ────────────────────────────────────────────────────

    def _scrape_ocv(self) -> List[ArrestRecord]:
        data = None
        for url in OCV_FEEDS:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=60)
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, list) and payload:
                    data = payload
                    logger.info(f"Sumner: loaded {len(data)} rows from {url.split('/')[-1]}")
                    break
            except Exception as e:
                logger.warning(f"Sumner OCV {url.split('/')[-1]}: {e}")

        if not data:
            logger.error("Sumner: all OCV feeds failed")
            return []

        records: List[ArrestRecord] = []
        seen: set = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            rec = self._parse_ocv_item(item)
            if not rec:
                continue
            key = rec.Booking_Number
            if not key or key in seen:
                continue
            seen.add(key)
            records.append(rec)
        return records

    def _parse_ocv_item(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        # Schema A: rtjb inmatesV3 (title / content HTML)
        if item.get("title") and item.get("content"):
            return self._parse_rtjb_item(item)
        # Schema B: SumnerInmates.json (Name / BookedNo / charges[])
        if item.get("Name") or item.get("BookedNo"):
            return self._parse_s3_inmate(item)
        return None

    def _parse_s3_inmate(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        name = (item.get("Name") or "").strip()
        booking = str(item.get("BookedNo") or "").strip()
        if not name or not booking:
            return None

        first, middle, last = self._pn(name)
        book_date = str(item.get("BookDate") or "").strip()
        book_time = ""
        if book_date:
            parts = book_date.split()
            book_date = parts[0]
            book_time = parts[1] if len(parts) > 1 else ""

        race = str(item.get("Race") or "")[:30]
        sex_raw = str(item.get("Gender") or item.get("Sex") or "")
        sex = sex_raw[0].upper() if sex_raw else ""
        age = str(item.get("Age") or "").strip()
        height = str(item.get("Height") or "").strip()
        weight = re.sub(
            r"\s*lbs?\s*",
            "",
            str(item.get("Weight") or item.get("weight") or ""),
            flags=re.I,
        ).strip()
        agency = str(item.get("ArrestAgency") or "").strip()
        if agency.upper() in ("N/A", "NA", ""):
            agency = AGENCY

        charges_list = item.get("charges") or []
        charge_names: List[str] = []
        total_bond = 0.0
        if isinstance(charges_list, list):
            for ch in charges_list:
                if not isinstance(ch, dict):
                    if isinstance(ch, str) and ch.strip():
                        charge_names.append(ch.strip())
                    continue
                desc = (
                    ch.get("ChargeDescription")
                    or ch.get("Description")
                    or ch.get("description")
                    or ch.get("charge")
                    or ch.get("Charge")
                    or ""
                )
                code = ch.get("ChargeCode") or ch.get("code") or ""
                label = str(desc).strip()
                if code and label and str(code) not in label:
                    label = f"{code} - {label}"
                elif not label and code:
                    label = str(code)
                if label:
                    charge_names.append(label)
                for bk in ("Bond", "bond", "BondAmount", "bond_amount", "Bail"):
                    if ch.get(bk) is not None:
                        try:
                            total_bond += float(
                                str(ch.get(bk)).replace("$", "").replace(",", "") or 0
                            )
                        except ValueError:
                            pass
                        break

        charges = "; ".join(charge_names[:15]) if charge_names else "Unknown"
        bond = (
            str(int(total_bond) if total_bond == int(total_bond) else total_bond)
            if total_bond
            else "0"
        )
        mug = str(item.get("ImageURL") or item.get("imageURL") or "")
        if "missing-image" in mug:
            mug = ""

        return ArrestRecord(
            County=self.county,
            State="TN",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking,
            Booking_Date=book_date,
            Booking_Time=book_time,
            Age_At_Arrest=age,
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Charges=charges,
            Bond_Amount=bond,
            Status="In Custody",
            Facility=FACILITY,
            Agency=agency,
            Mugshot_URL=mug,
            Detail_URL=PORTAL_URL,
            Person_ID=booking,
            LastCheckedMode="INITIAL",
        )

    def _parse_rtjb_item(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        title = (item.get("title") or "").strip()
        if not title or title.lower() in ("oops!",):
            return None

        content = str(item.get("content") or "")
        fields = self._parse_content_html(content)

        inmate_id = fields.get("inmate_id") or ""
        # Require official source inmate ID
        if not inmate_id:
            return None

        booking = inmate_id
        first, middle, last = self._pn(title)
        charges = fields.get("charges") or "Unknown"
        bond = fields.get("bond") or "0"
        book_date = fields.get("booking_date") or ""
        race = (fields.get("race") or "")[:30]
        sex_raw = fields.get("sex") or ""
        sex = sex_raw[0].upper() if sex_raw else ""
        age = fields.get("age") or ""

        oid = ""
        _id = item.get("_id")
        if isinstance(_id, dict):
            oid = str(_id.get("$id") or "").strip()
        elif _id:
            oid = str(_id).strip()

        detail = f"{PORTAL_URL}/{oid}" if oid else PORTAL_URL

        mug = ""
        images = item.get("images") or []
        if isinstance(images, list) and images:
            img0 = images[0] if isinstance(images[0], dict) else {}
            large = str(img0.get("large") or img0.get("small") or "")
            if large and "missing-image" not in large:
                mug = large

        return ArrestRecord(
            County=self.county,
            State="TN",
            Full_Name=title,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=str(booking),
            Booking_Date=book_date,
            Age_At_Arrest=age,
            Race=race,
            Sex=sex,
            Charges=charges,
            Bond_Amount=str(bond).replace("$", "").replace(",", "") or "0",
            Status="In Custody",
            Facility=FACILITY,
            Agency=AGENCY,
            Mugshot_URL=mug,
            Detail_URL=detail,
            Person_ID=inmate_id,
            LastCheckedMode="INITIAL",
        )

    def _parse_content_html(self, html: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not html:
            return out
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        patterns = {
            "inmate_id": r"Inmate ID:\s*(\d+)",
            "race": r"Race:\s*([^\n]+)",
            "sex": r"Sex:\s*([^\n]+)",
            "age": r"Age:\s*([^\n]+)",
            "booking_date": r"Booking Date:\s*([0-9/\-:\sAPMapm]+)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                out[key] = m.group(1).strip()

        # Charges section — OCV often: "Description: CODE - CHARGE\nBond: $X"
        if re.search(r"Charges:\s*", text, re.I):
            after = re.split(r"Charges:\s*", text, maxsplit=1, flags=re.I)[1]
            lines = []
            bonds: List[float] = []
            for ln in after.splitlines():
                ln = ln.strip()
                if not ln:
                    if lines:
                        break
                    continue
                if ln.lower().startswith(("information", "inmate id", "race:", "sex:")):
                    break
                # Check for bond line
                if re.search(r"^Bond:\s*", ln, re.I):
                    bm = re.search(r"Bond:\s*[$]?\s*([\d,]+(?:\.\d{2})?)", ln, re.I)
                    if bm:
                        try:
                            bonds.append(float(bm.group(1).replace(",", "")))
                        except ValueError:
                            pass
                    continue
                ln = re.sub(r"^[\-\•\*]+\s*", "", ln)
                ln = re.sub(r"^Description:\s*", "", ln, flags=re.I)
                if ln:
                    lines.append(ln)
            if lines:
                out["charges"] = "; ".join(lines[:12])
            if bonds:
                out["bond"] = str(int(sum(bonds)) if sum(bonds) == int(sum(bonds)) else sum(bonds))

        if "bond" not in out:
            bm = re.search(r"[$]\s*([\d,]+(?:\.\d{2})?)", text)
            if bm:
                out["bond"] = bm.group(1).replace(",", "")
        return out

    # ── Fallback: HTML ?page=N walk ──────────────────────────────────────────

    def _scrape_html_pages(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []
        seen = set()

        for page in range(1, MAX_HTML_PAGES + 1):
            url = f"{PORTAL_URL}?page={page}"
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code != 200:
                    break
                page_recs = self._parse_html_page(resp.text)
                if not page_recs:
                    break
                new_in_page = 0
                for r in page_recs:
                    if r.Booking_Number and r.Booking_Number not in seen:
                        seen.add(r.Booking_Number)
                        records.append(r)
                        new_in_page += 1
                if new_in_page == 0:
                    break
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"Sumner HTML page {page}: {e}")
                break

        return records

    def _parse_html_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[ArrestRecord] = []

        cards = (
            soup.find_all("div", class_=re.compile(r"inmate|offender|card|item", re.I))
            or soup.find_all("article")
        )
        for card in cards:
            text = card.get_text(" ", strip=True)
            if not text or len(text) < 20:
                continue

            # Look for Inmate ID
            m_id = re.search(r"(?:Inmate\s*ID|Book(?:ing)?\s*#?)\s*[:#]?\s*(\d+)", text, re.I)
            if not m_id:
                continue
            booking = m_id.group(1)

            # Name from header
            hdr = card.find(["h2", "h3", "h4", "h5", "a"])
            name_raw = hdr.get_text(strip=True) if hdr else ""
            if not name_raw or len(name_raw) < 3:
                continue

            name = self._clean_name(name_raw)
            first, middle, last = self._pn(name)

            m_date = re.search(r"Book(?:ed|ing)?\s*(?:Date)?\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
            book_date = m_date.group(1) if m_date else ""

            m_bond = re.search(r"[$]\s*([\d,]+(?:\.\d{2})?)", text)
            bond = m_bond.group(1).replace(",", "") if m_bond else "0"

            out.append(
                ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Person_ID=str(booking),
                    Booking_Date=book_date,
                    Charges="Unknown",
                    Bond_Amount=bond,
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency=AGENCY,
                    Detail_URL=PORTAL_URL,
                )
            )

        return out

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_name(raw: str) -> str:
        if not raw:
            return ""
        cleaned = re.sub(r"[\xa0\s]+", " ", raw).strip()
        return re.sub(r"\s+", " ", cleaned)

    @staticmethod
    def _pn(full_name: str) -> tuple[str, str, str]:
        if not full_name:
            return ("", "", "")
        if "," in full_name:
            parts = [p.strip() for p in full_name.split(",", 1)]
            last = parts[0]
            rest = parts[1].split() if len(parts) > 1 else []
            first = rest[0] if rest else ""
            middle = " ".join(rest[1:]) if len(rest) > 1 else ""
            return (first, middle, last)
        parts = full_name.split()
        if len(parts) == 1:
            return (parts[0], "", "")
        if len(parts) == 2:
            return (parts[0], "", parts[1])
        return (parts[0], " ".join(parts[1:-1]), parts[-1])
