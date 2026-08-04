"""
Connecticut Department of Correction (CT DOC) Inmate Scraper.

Portal: https://www.ctinmateinfo.state.ct.us/
Coverage: Statewide CT correctional facilities (Bridgeport CC, Hartford CC,
          New Haven CC, Corrigan-Radgowski, MacDougall-Walker, York CI, etc.)

Strategy (hardened 2026-08-04):
  1. Letter A–Z last-name search → parse results **list** (Number, Name, DOB, Facility)
  2. Optional detail enrichment for a capped sample (bond + controlling offense)
  3. Dedup by inmate number

List search alone returns hundreds per letter (verified: A≈600, B≈1100, S≈1200).
Prior detail-only path was too slow (3 prefixes × 100 details) for effective coverage.

Dedup key: Inmate Number → Booking_Number
Dashboard label: ``CT DOC (CT)``
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

from curl_cffi import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.ctinmateinfo.state.ct.us/"
POST_URL = "https://www.ctinmateinfo.state.ct.us/resultsupv.asp"
DETAIL_BASE = "https://www.ctinmateinfo.state.ct.us/"

# Detail enrichment is optional — list rows already have name/DOB/facility.
MAX_DETAIL_ENRICH = 80
DETAIL_DELAY_S = 0.2
LETTER_DELAY_S = 0.35
# M0 storage guard: never dump the full ~14k statewide sentenced population.
# Local CCs hold most unsentenced / bondable detainees (actionable bail leads).
MAX_RECORDS_PER_RUN = int(__import__("os").environ.get("CT_DOC_MAX_RECORDS", "2500"))
# Facilities that look like local community / CC sites (not long-term CI)
_CC_HINTS = (" CC", "CORRECTIONAL CENTER", "DETENTION", "LOCKUP", "JAIL")


class CTDOCInmateScraper(BaseScraper):
    """Scrapes the CT DOC public inmate information search (statewide)."""

    enrich_details: bool = True
    # When True, only write CC-style facilities + enriched rows (default; saves M0 space)
    prefer_local_facilities: bool = True

    @property
    def county(self) -> str:
        return "CT DOC"

    @property
    def state(self) -> str:
        return "CT"

    @property
    def scraper_id(self) -> str:
        # Stable id: avoid scraper_ct_ct_doc from state+slug("CT DOC")
        return "scraper_ct_doc"

    @staticmethod
    def _is_local_facility(facility: str) -> bool:
        """True for local detention / CC sites (bondable), not long-term CI prisons."""
        f = (facility or "").upper().strip()
        if not f:
            return False
        # Explicit long-term CI prisons → exclude from prefer_local filter
        if " CI" in f or f.endswith(" CI") or "CORRECTIONAL INSTITUTION" in f:
            # Still keep women's York CI as some bondable holds exist — include if "YORK"
            if "YORK" in f:
                return True
            return False
        if any(h in f for h in _CC_HINTS):
            return True
        # Named CCs without the letters " CC" glued oddly
        for token in ("BRIDGEPORT", "HARTFORD", "NEW HAVEN", "NEW BRITAIN", "GARNER"):
            if token in f:
                return True
        return False

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session(impersonate="chrome124")
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        try:
            session.get(SEARCH_URL, timeout=20, verify=False)
        except Exception as exc:
            logger.error("CT DOC landing GET failed: %s", exc)
            return []

        by_id: Dict[str, dict] = {}
        for letter in string.ascii_uppercase:
            try:
                rows = self._search_list(session, letter)
                for row in rows:
                    iid = row["inmate_id"]
                    if not iid:
                        continue
                    if iid not in by_id:
                        by_id[iid] = row
                logger.info(
                    "  CT DOC '%s': +%d list (unique total %d)",
                    letter,
                    len(rows),
                    len(by_id),
                )
                time.sleep(LETTER_DELAY_S)
            except Exception as exc:
                logger.warning("CT DOC letter '%s' failed: %s", letter, exc)

        if not by_id:
            logger.warning("CT DOC: no inmates from letter walk")
            return []

        # Prefer local CC facilities for write (bondable population)
        if self.prefer_local_facilities:
            local = {
                k: v for k, v in by_id.items()
                if self._is_local_facility(v.get("facility") or "")
            }
            if local:
                logger.info(
                    "  CT DOC facility filter: %d local / %d total (dropping long-term CI bulk)",
                    len(local),
                    len(by_id),
                )
                by_id = local

        # Optional detail enrichment (bond / offense) on a capped sample
        enrich_ids: List[str] = []
        if self.enrich_details and MAX_DETAIL_ENRICH > 0:
            enrich_ids = sorted(
                by_id.keys(),
                key=lambda i: (0 if self._is_local_facility(by_id[i].get("facility") or "") else 1, i),
            )[:MAX_DETAIL_ENRICH]
            for iid in enrich_ids:
                try:
                    detail = self._parse_detail(session, iid)
                    if detail:
                        by_id[iid].update(detail)
                    time.sleep(DETAIL_DELAY_S)
                except Exception as exc:
                    logger.debug("CT DOC detail %s failed: %s", iid, exc)

        # Cap write volume for M0 — prioritize enriched / local / any remaining
        rows_sorted = sorted(
            by_id.values(),
            key=lambda r: (
                0 if r.get("charges") and r.get("charges") != "Unknown" else 1,
                0 if self._is_local_facility(r.get("facility") or "") else 1,
                0 if (r.get("bond") and r.get("bond") not in ("0", "", "0.0")) else 1,
                r.get("inmate_id") or "",
            ),
        )[:MAX_RECORDS_PER_RUN]

        records = []
        for row in rows_sorted:
            rec = self._to_record(row)
            if rec and rec.Booking_Number:
                records.append(rec)

        logger.info(
            "✅ CT DOC: %d records written (from %d scanned, enriched %d) in %.1fs",
            len(records),
            len(by_id),
            len(enrich_ids),
            time.time() - start,
        )
        return records

    def _search_list(self, session: requests.Session, last_prefix: str) -> List[dict]:
        payload = {
            "id_inmt_num": "",
            "nm_inmt_last": last_prefix,
            "nm_inmt_first": "",
            "dt_inmt_birth": "",
            "submit1": "Search All Inmates",
        }
        resp = session.post(POST_URL, data=payload, timeout=45, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return self._parse_results_table(soup)

    def _parse_results_table(self, soup: BeautifulSoup) -> List[dict]:
        """Parse Number | Inmate Name | Date of Birth | Facility rows."""
        out: List[dict] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 3:
                continue
            # Find header row that looks like results
            header_idx = None
            for i, tr in enumerate(rows[:5]):
                texts = [c.get_text(" ", strip=True).lower() for c in tr.find_all(["td", "th"])]
                joined = " ".join(texts)
                if "number" in joined and ("name" in joined or "inmate" in joined):
                    header_idx = i
                    break
            if header_idx is None:
                continue

            for tr in rows[header_idx + 1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 3:
                    continue
                # Inmate number may be in first cell or link
                a = tr.find("a", href=True)
                inmate_id = ""
                if a and "id_inmt_num=" in a["href"]:
                    inmate_id = a["href"].split("id_inmt_num=")[-1].split("&")[0].strip()
                if not inmate_id:
                    for c in cells:
                        if c.isdigit() and len(c) >= 4:
                            inmate_id = c
                            break
                if not inmate_id:
                    continue

                # Name: usually second cell, or cell that contains a comma
                name = ""
                dob = ""
                facility = ""
                for c in cells:
                    if c == inmate_id or c.lower().startswith("click"):
                        continue
                    if not name and ("," in c or re.search(r"[A-Za-z]{2,}", c)):
                        # Prefer comma form LAST,FIRST
                        if "," in c or not any(ch.isdigit() for ch in c):
                            if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", c):
                                pass
                            elif c.lower() in ("number", "inmate name", "date of birth", "facility"):
                                continue
                            else:
                                name = c
                                continue
                    if not dob and re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", c):
                        dob = c
                        continue
                    if name and dob and not facility and len(c) > 2:
                        facility = c

                if not name:
                    # positional fallback: Number, Name, DOB, Facility
                    if len(cells) >= 4:
                        name = cells[1]
                        dob = cells[2]
                        facility = cells[3]
                    elif len(cells) >= 2:
                        name = cells[1]

                if not name or len(name) < 2:
                    continue

                out.append({
                    "inmate_id": inmate_id,
                    "name": name,
                    "dob": dob,
                    "facility": facility or "CT DOC Facility",
                    "detail_url": urljoin(DETAIL_BASE, f"detailsupv.asp?id_inmt_num={inmate_id}"),
                })
            if out:
                break
        return out

    def _parse_detail(self, session: requests.Session, inmate_id: str) -> Optional[dict]:
        url = urljoin(DETAIL_BASE, f"detailsupv.asp?id_inmt_num={inmate_id}")
        resp = session.get(url, timeout=20, verify=False)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        data: Dict[str, str] = {}
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            key = cells[0].rstrip(":").strip().lower()
            val = cells[1].strip()
            if key and val and key != val.lower().rstrip(":"):
                data[key] = val

        # Also try label: value patterns in page text
        text = soup.get_text("\n", strip=True)
        for label, field in [
            (r"Bond Amount:\s*(.+)", "bond amount"),
            (r"Controlling Offense\*?:\s*(.+)", "controlling offense"),
            (r"Status:\s*(.+)", "status"),
            (r"Current Location:\s*(.+)", "current location"),
            (r"Latest Admission Date:\s*(.+)", "latest admission date"),
        ]:
            m = re.search(label, text, re.I)
            if m and field not in data:
                data[field] = m.group(1).strip().split("\n")[0].strip()

        out: Dict[str, str] = {}
        if data.get("bond amount"):
            out["bond"] = self._normalize_bond(data["bond amount"])
        offense = (
            data.get("controlling offense*")
            or data.get("controlling offense")
            or ""
        )
        if offense:
            out["charges"] = re.sub(r"\s+", " ", offense).strip()
        if data.get("status"):
            out["status_raw"] = data["status"]
        if data.get("current location"):
            out["facility"] = data["current location"]
        if data.get("latest admission date"):
            out["admit"] = data["latest admission date"]
        return out or None

    def _to_record(self, row: dict) -> ArrestRecord:
        name = row["name"]
        first, last = self._split_name(name)
        status_raw = (row.get("status_raw") or "").upper()
        custody = "In Custody"
        if "UNSENTENCED" in status_raw:
            custody = "In Custody (Unsentenced)"
        elif "SENTENCED" in status_raw:
            custody = "In Custody (Sentenced)"

        bond = row.get("bond") or "0"
        charges = row.get("charges") or "Unknown"

        # Normalize "LAST,FIRST M" → "Last, First M"
        if "," in name:
            lp, rp = name.split(",", 1)
            display = f"{lp.strip().title()}, {rp.strip().title()}"
        else:
            display = name.title() if name.isupper() else name

        return ArrestRecord(
            County=self.county,
            State="CT",
            Full_Name=display,
            First_Name=first,
            Last_Name=last,
            DOB=row.get("dob") or "",
            Booking_Number=str(row["inmate_id"]),
            Person_ID=str(row["inmate_id"]),
            Facility=row.get("facility") or "CT DOC Facility",
            Status=custody,
            Charges=charges,
            Bond_Amount=bond if bond else "0",
            Booking_Date=row.get("admit") or "",
            Agency="Connecticut Department of Correction",
            Detail_URL=row.get("detail_url") or SEARCH_URL,
        )

    @staticmethod
    def _normalize_bond(val: str) -> str:
        if not val:
            return "0"
        clean = "".join(c for c in str(val) if c.isdigit() or c == ".")
        if not clean or clean == "0" or clean == "0.0":
            return "0"
        return clean

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        """'LAST,FIRST MIDDLE' → (first, last)."""
        name = name.strip()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        parts = name.split()
        if len(parts) >= 2:
            return parts[0].title(), parts[-1].title()
        return name.title(), ""
