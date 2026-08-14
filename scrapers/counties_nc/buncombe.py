"""
Buncombe County (NC) Arrest Scraper — Police-to-Citizen SPA (Asheville).

Portal: https://buncombecountyso.policetocitizen.com/Inmates/Catalog

Angular SPA shell; roster data requires client render. Strategy:
1) Stealth GET + strict table parse (reject bond amounts / nav as names)
2) DrissionPage letter walk on last-name filter
3) Fail closed (no fabricated inmates)
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import List, Set

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://buncombecountyso.policetocitizen.com/Inmates/Catalog"
FACILITY = "Buncombe County Detention Facility"
AGENCY = "Buncombe County Sheriff's Office"

BAD_NAME = re.compile(
    r"^[\d\$\.,\s%]+$|skip to|logout|search|inmate catalog|police to citizen|"
    r"^name$|^booking|^charge|^bond|^status|^age$|^sex$|^race$",
    re.I,
)


class BuncombeScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Buncombe"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        try:
            from scrapers.stealth_utils import make_stealth_request

            resp = make_stealth_request(PORTAL_URL, method="GET", timeout=30)
            if resp and resp.status_code == 200:
                records = self._parse_html(resp.text, seen)
        except Exception as e:
            logger.debug(f"Buncombe stealth GET: {e}")

        if not records:
            records = self._scrape_browser(seen)

        # Drop garbage rows that slipped through (bond-as-name etc.)
        records = [r for r in records if self._looks_like_name(r.Full_Name)]

        logger.info(f"✅ Buncombe (NC): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_browser(self, seen: Set[str]) -> List[ArrestRecord]:
        out: List[ArrestRecord] = []
        try:
            from DrissionPage import ChromiumPage
        except Exception as e:
            logger.error(f"Buncombe: DrissionPage unavailable: {e}")
            return out

        page = None
        try:
            co = self._get_browser_options() if hasattr(self, "_get_browser_options") else None
            page = ChromiumPage(co) if co else ChromiumPage()
            page.get(PORTAL_URL)
            time.sleep(3)
            for letter in list(string.ascii_uppercase)[:15]:
                try:
                    filled = False
                    for sel in (
                        "#LastName",
                        "input[name=LastName]",
                        "input[formcontrolname=lastName]",
                        "input[placeholder*=Last]",
                        "input[placeholder*=last]",
                    ):
                        el = page.ele(sel, timeout=1)
                        if el:
                            el.clear()
                            el.input(letter)
                            filled = True
                            break
                    if filled:
                        btn = (
                            page.ele("tag:button@@text():Search", timeout=1)
                            or page.ele("css:button[type=submit]", timeout=1)
                            or page.ele("text:Search", timeout=1)
                        )
                        if btn:
                            btn.click()
                            time.sleep(1.8)
                    batch = self._parse_html(page.html or "", seen)
                    out.extend(batch)
                except Exception as e:
                    logger.debug(f"Buncombe letter {letter}: {e}")
                time.sleep(0.25)
            out.extend(self._parse_html(page.html or "", seen))
        except Exception as e:
            logger.error(f"Buncombe browser scrape failed: {e}")
        finally:
            try:
                if page:
                    page.quit()
            except Exception:
                pass
        return out

    def _parse_html(self, html: str, seen: Set[str]) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []

        # Card / list items with name + booking cues
        for el in soup.select(
            "[class*='inmate'], [class*='Inmate'], [class*='catalog'] tr, table tr, mat-row, .list-item"
        ):
            text = el.get_text(" ", strip=True)
            if len(text) < 5:
                continue
            # extract name-like token
            name = ""
            for chunk in re.split(r"\s{2,}|\|", text):
                chunk = chunk.strip()
                if self._looks_like_name(chunk) and ("," in chunk or len(chunk.split()) >= 2):
                    name = chunk
                    break
            if not name:
                continue
            booking = ""
            m = re.search(r"\b(\d{5,})\b", text)
            if m:
                booking = m.group(1)
            if not booking:
                continue
            if booking in seen:
                continue
            seen.add(booking)
            first, middle, last = self._pn(name)
            bond = "0"
            bm = re.search(r"\$([\d,]+)", text)
            if bm:
                bond = bm.group(1).replace(",", "")
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="NC",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Bond_Amount=bond,
                    Charges="Unknown",
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency=AGENCY,
                    Detail_URL=PORTAL_URL,
                    LastCheckedMode="INITIAL",
                )
            )

        if records:
            return records

        # Classic tables
        for table in soup.find_all("table"):
            for tr in table.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = next((c for c in cells if self._looks_like_name(c)), "")
                if not name:
                    continue
                booking = next((c for c in cells if re.match(r"^\d{4,}$", c)), "")
                if not booking:
                    continue
                if booking in seen:
                    continue
                seen.add(booking)
                first, middle, last = self._pn(name)
                bond = "0"
                bm = re.search(r"\$([\d,]+)", " ".join(cells))
                if bm:
                    bond = bm.group(1).replace(",", "")
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="NC",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Bond_Amount=bond,
                        Charges="Unknown",
                        Status="In Custody",
                        Facility=FACILITY,
                        Agency=AGENCY,
                        Detail_URL=PORTAL_URL,
                        LastCheckedMode="INITIAL",
                    )
                )
        return records

    @staticmethod
    def _looks_like_name(s: str) -> bool:
        if not s or len(s) < 3 or len(s) > 90:
            return False
        if BAD_NAME.search(s.strip()):
            return False
        if re.match(r"^\$", s.strip()):
            return False
        if not re.search(r"[A-Za-z]{2,}", s):
            return False
        if "," in s:
            return True
        words = [w for w in re.split(r"\s+", s) if re.search(r"[A-Za-z]{2,}", w)]
        return len(words) >= 2

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
