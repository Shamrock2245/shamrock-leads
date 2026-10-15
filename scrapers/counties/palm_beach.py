"""
Palm Beach County Arrest Scraper — PBSO Booking Blotter (ColdFusion).
Source: Palm Beach County Sheriff's Office
URL: https://www3.pbso.org/blotter/index.cfm
Method: plain HTTPS via ``scrapers.plain_http`` (honest User-Agent, certificate
verified). No browser, no Chrome disguise, no stealth, no CAPTCHA handling.

Flow: GET the blotter, read the search form the page itself publishes
(action, method, hidden fields), submit it with ``start_date`` / ``end_date``
(MM/DD/YYYY), parse result cards with ``scrapers.pbso_parse.parse_pbso_html``,
and follow the page's own numbered / » pagination links.

Failure is visible, never worked around:
  * 401/403/429 or a challenge page (including hCaptcha) -> ``egress_block:``
  * TLS handshake dropped -> ``egress_block: ... connection_reset:``
  * certificate verification failure -> ``tls_cert_invalid:``
  * search form missing from the page -> ``PalmBeachSourceChanged``
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrapers import plain_http
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import EgressBlocked
from scrapers.pbso_parse import (
    BLOTTER_URL,
    COUNTY,
    FACILITY,
    extract_pbso_booking_id,
    is_pbso_index_only,
    parse_name,
    parse_pbso_html,
)
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

DAYS_BACK = 2  # Search today + yesterday


class PalmBeachSourceChanged(RuntimeError):
    """The blotter page no longer matches the published form/result contract."""


class PalmBeachCountyScraper(BaseScraper):
    """Palm Beach County — PBSO Booking Blotter (www3.pbso.org)"""

    @property
    def county(self) -> str:
        return "Palm Beach"

    def scrape(self) -> List[ArrestRecord]:
        session = plain_http.make_session({"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"})
        records = []
        for i in range(DAYS_BACK - 1, -1, -1):
            target_date = (datetime.now() - timedelta(days=i)).strftime("%m/%d/%Y")
            for row in self._search_and_collect(session, target_date, max_pages=50):
                records.append(self._row_to_arrest_record(row, mode="scrape"))
        logger.info(f"Palm Beach: total {len(records)} records")
        return records

    # ── HTTP ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get(session, method: str, url: str, **kwargs):
        """One verified request; blocks and challenge pages raise, never retried around."""
        resp = plain_http.request(session, method, url, county="Palm Beach", **kwargs)
        if "hcaptcha" in (resp.text or "")[:200000].lower():
            raise EgressBlocked("egress_block: Palm Beach challenge page (hCaptcha)")
        if resp.status_code != 200:
            raise PalmBeachSourceChanged(f"Palm Beach: HTTP {resp.status_code} from {url}")
        return resp

    # ── Search & Collection ────────────────────────────────────────────────

    def _search_and_collect(self, session, target_date: str, max_pages: int = 50) -> list:
        logger.info(f"Palm Beach: searching {target_date}")
        index = self._get(session, "GET", BLOTTER_URL)
        method, action, fields = self._search_form(index.text, index.url or BLOTTER_URL)
        fields["start_date"] = target_date
        fields["end_date"] = target_date
        if method == "post":
            resp = self._get(session, "POST", action, data=fields)
        else:
            resp = self._get(session, "GET", action, params=fields)

        all_rows, seen_pages = [], set()
        for page_no in range(1, max_pages + 1):
            rows = parse_pbso_html(resp.text)
            logger.info(f"Palm Beach: page {page_no} → {len(rows)} records")
            all_rows.extend(r for r in rows if r.get("booking_num"))
            nxt = self._next_page_url(resp.text, resp.url or action)
            if not nxt or nxt in seen_pages:
                break
            seen_pages.add(nxt)
            resp = self._get(session, "GET", nxt)
        logger.info(f"Palm Beach: collected {len(all_rows)} records for {target_date}")
        return all_rows

    @staticmethod
    def _search_form(html: str, base_url: str):
        """The page's own date-search form: (method, absolute action, field values)."""
        soup = BeautifulSoup(html or "", "html.parser")
        for form in soup.find_all("form"):
            names = {el.get("name") or el.get("id") for el in form.find_all(["input", "select"])}
            if "start_date" in names and "end_date" in names:
                fields = {}
                for el in form.find_all(["input", "select"]):
                    name = el.get("name") or el.get("id")
                    if not name or (el.get("type") or "").lower() in ("submit", "button", "image", "reset"):
                        continue
                    fields[name] = el.get("value") or ""
                method = (form.get("method") or "get").strip().lower()
                return method, urljoin(base_url, form.get("action") or base_url), fields
        raise PalmBeachSourceChanged("Palm Beach: blotter search form (start_date/end_date) not found")

    @staticmethod
    def _next_page_url(html: str, base_url: str) -> Optional[str]:
        """Follow the page's own pagination link (next number or »); None when last."""
        soup = BeautifulSoup(html or "", "html.parser")
        m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", soup.get_text(" "))
        if m and int(m.group(1)) >= int(m.group(2)):
            return None
        wanted = {str(int(m.group(1)) + 1)} if m else set()
        wanted.add("»")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if a.get_text(strip=True) in wanted and href and not href.lower().startswith("javascript:"):
                return urljoin(base_url, href)
        return None

    # ── Result Card Parsing ────────────────────────────────────────────────

    def _row_to_arrest_record(self, row: dict, mode: str = "scrape") -> ArrestRecord:
        booking = str(row.get("booking_num") or "")
        detail = f"{BLOTTER_URL}?booking={booking}" if booking else BLOTTER_URL
        return ArrestRecord(
            County=COUNTY,
            State="FL",
            Facility=row.get("facility", FACILITY),
            Agency=row.get("agency", "PBSO"),
            Full_Name=row.get("full_name", ""),
            First_Name=row.get("first_name", ""),
            Middle_Name=row.get("middle_name", ""),
            Last_Name=row.get("last_name", ""),
            Booking_Number=booking,
            Person_ID=row.get("jacket_num", ""),
            DOB=row.get("dob", ""),
            Race=row.get("race", ""),
            Sex=row.get("sex", ""),
            Booking_Date=row.get("booking_date", ""),
            Booking_Time=row.get("booking_time", ""),
            Arrest_Date=row.get("booking_date", ""),
            Arrest_Time=row.get("booking_time", ""),
            Status=row.get("status") or "",  # unknown is ""; never default In Custody
            Release_Date=row.get("release_date", ""),
            Charges=row.get("charges", ""),
            Bond_Amount=row.get("bond_amount", ""),  # unknown is ""; a published "0" is kept
            Mugshot_URL=row.get("mug_url", ""),
            Detail_URL=detail,
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
            LastChecked=datetime.now(timezone.utc).isoformat(),
            LastCheckedMode=mode,
        )

    def _fetch_single_booking(self, booking_id: str, detail_url: str):
        """Re-fetch one PBSO booking from a booking-tagged public URL.

        The blotter search form is not a per-inmate page. Index-only URLs
        are not fetched (no generic GET, no browser, no unpublished probe).
        Returns None when the HTML has no matching name+booking card.
        """
        if not booking_id:
            return None
        url = (detail_url or "").strip() or BLOTTER_URL
        tagged = extract_pbso_booking_id(url)
        if is_pbso_index_only(url) and not tagged:
            return None
        if tagged and tagged != str(booking_id).strip():
            return None
        session = plain_http.make_session({"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"})
        # Blocks / TLS failures raise (egress_block / tls_cert_invalid); they are
        # not turned into "not found".
        resp = plain_http.request(session, "GET", url, county="Palm Beach", timeout=15)
        if resp is None or resp.status_code != 200:
            return None
        rows = parse_pbso_html(resp.text)
        wanted = str(booking_id).strip()
        matches = [r for r in rows if str(r.get("booking_num") or "") == wanted]
        if len(matches) != 1:
            return None
        record = self._row_to_arrest_record(matches[0], mode="UPDATE")
        record.LastCheckedMode = "UPDATE"
        return record

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_name(name):
        return parse_name(name)
