"""
Sumner County (TN) Arrest Scraper — MyOCV inmatesV3 JSON feed.

Primary: https://apps.myocv.com/feed/rtjb/a46036101/inmatesV3  (~700 inmates)
Fallback: HTML pagination on https://www.sumnersherifftn.gov/inmates?page=N
"""
from __future__ import annotations

import hashlib
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

# Prefer S3 dump (full roster); rtjb feed as secondary
OCV_FEEDS = (
    "https://myocv.s3.us-east-1.amazonaws.com/ocvapps/a46036101/SumnerInmates.json",
    "https://myocv.s3.amazonaws.com/ocvapps/a46036101/SumnerInmates.json",
    "https://apps.myocv.com/feed/rtjb/a46036101/inmatesV3",
)
PORTAL_URL = "https://www.sumnersherifftn.gov/inmates"
FACILITY = "Sumner County Jail"
AGENCY = "Sumner County Sheriff's Office"
MAX_HTML_PAGES = 40
MAX_DETAILS = 40  # HTML fallback only

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}


class SumnerScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The configured Sumner OCV and public paths did not establish a complete "
        "booking-safe broad listing through ordinary access."
    )

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
            key = rec.Booking_Number or rec.Full_Name
            if not key or key in seen:
                continue
            seen.add(key)
            records.append(rec)
        return records

    def _parse_ocv_item(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        # Schema A: SumnerInmates.json (Name / BookedNo / charges[])
        if item.get("Name") or item.get("BookedNo"):
            return self._parse_s3_inmate(item)
        # Schema B: rtjb inmatesV3 (title / content HTML)
        return self._parse_rtjb_item(item)

    def _parse_s3_inmate(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        name = (item.get("Name") or "").strip()
        if not name:
            return None
        booking = str(item.get("BookedNo") or "").strip()
        if not booking:
            booking = hashlib.sha1(f"sumner|{name}".encode()).hexdigest()[:16]

        first, middle, last = self._pn(name)
        book_date = str(item.get("BookDate") or "").strip()
        if book_date:
            # "03/25/2024 16:22" → date + time
            parts = book_date.split()
            book_date = parts[0]
            book_time = parts[1] if len(parts) > 1 else ""
        else:
            book_time = ""

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
        oid = ""
        _id = item.get("_id")
        if isinstance(_id, dict):
            oid = str(_id.get("$id") or "").strip()
        elif _id:
            oid = str(_id).strip()

        booking = inmate_id or oid
        if not booking:
            booking = hashlib.sha1(f"sumner|{title}".encode()).hexdigest()[:16]

        first, middle, last = self._pn(title)
        charges = fields.get("charges") or "Unknown"
        bond = fields.get("bond") or "0"
        book_date = fields.get("booking_date") or ""
        race = (fields.get("race") or "")[:30]
        sex_raw = fields.get("sex") or ""
        sex = sex_raw[0].upper() if sex_raw else ""
        age = fields.get("age") or ""

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
            Person_ID=inmate_id or oid,
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
                bm = re.search(r"Bond:\s*\$?\s*([\d,]+(?:\.\d{2})?)", ln, re.I)
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
            bm = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
            if bm:
                out["bond"] = bm.group(1).replace(",", "")
        return out

    # ── Fallback: HTML ?page=N walk ──────────────────────────────────────────

    def _scrape_html_pages(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []
        seen: set = set()
        links: List[tuple] = []

        for page in range(1, MAX_HTML_PAGES + 1):
            url = PORTAL_URL if page == 1 else f"{PORTAL_URL}?page={page}"
            try:
                resp = session.get(url, timeout=40)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Sumner HTML page {page}: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            page_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r"/inmates/[a-f0-9]{16,}", href):
                    name = a.get_text(" ", strip=True)
                    if name and len(name) > 2:
                        page_links.append((urljoin(PORTAL_URL, href), name))

            # dedup within page then global
            new_count = 0
            seen_href = {h for h, _ in links}
            for href, name in page_links:
                if href in seen_href:
                    continue
                seen_href.add(href)
                links.append((href, name))
                new_count += 1

            if new_count == 0:
                break
            time.sleep(0.2)

        for i, (href, name) in enumerate(links):
            booking = href.rstrip("/").split("/")[-1][:24]
            if booking in seen:
                continue
            seen.add(booking)
            first, middle, last = self._pn(name)
            charges = "Unknown"
            book_date = ""
            bond = "0"
            inmate_id = ""

            if i < MAX_DETAILS:
                try:
                    detail = self._fetch_detail(session, href)
                    charges = detail.get("charges") or charges
                    book_date = detail.get("booking_date") or book_date
                    bond = detail.get("bond") or bond
                    inmate_id = detail.get("inmate_id") or ""
                    time.sleep(0.1)
                except Exception as e:
                    logger.debug(f"Sumner detail {href}: {e}")

            if inmate_id:
                booking = inmate_id

            records.append(
                ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Booking_Date=book_date,
                    Charges=charges,
                    Bond_Amount=str(bond).replace("$", "").replace(",", "") or "0",
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency=AGENCY,
                    Detail_URL=href,
                    LastCheckedMode="INITIAL",
                )
            )
        return records

    def _fetch_detail(self, session: requests.Session, url: str) -> dict:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        return self._parse_content_html(resp.text)

    @staticmethod
    def _pn(n: str):
        n = " ".join((n or "").strip().split())
        if "," in n:
            last, rest = n.split(",", 1)
            p = rest.strip().split()
            return (p[0] if p else ""), (" ".join(p[1:]) if len(p) > 1 else ""), last.strip()
        p = n.split()
        return (
            (p[0] if p else ""),
            (" ".join(p[1:-1]) if len(p) > 2 else ""),
            (p[-1] if len(p) > 1 else n),
        )
