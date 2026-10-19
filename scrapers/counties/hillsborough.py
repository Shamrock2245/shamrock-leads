"""
Hillsborough County (FL) — HCSO Arrest Inquiry (fail closed).
=============================================================
Source: https://webapps.hcso.tampa.fl.us/arrestinquiry/

Fail closed (2026-10-14, docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md):
the only path needs an account login, a solved reCAPTCHA v2 and a SOCKS /
residential proxy fallback. Login, CAPTCHA solving (owner-approved only for
Broward/Lake) and proxies are not allowed.

2026-10-16: the curl_cffi impersonation, Chrome UA, proxy resolver, login /
cookie code and the SolveCaptcha client are removed. There is no public,
unauthenticated request to move to plain_http, so this module makes no source
request at all; ``scrape`` returns [] behind the guard. Only the results-table
parser stays (pure, no network) for a future public contract:
* Status only from a published RELEASE DATE (status_source recorded); else "".
* Bond is the sum of the per-charge bond cells only when every cell publishes a
  dollar amount (a published $0 kept as "0"); otherwise "" — never "0".
* Booking_Number is the source booking number cell.
"""
import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup  # noqa: F401  (parser input type)

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry"
SEARCH_URL = f"{BASE_URL}/"


def _published_amount(text: str) -> Optional[float]:
    m = re.fullmatch(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", (text or "").strip())
    return float(m.group(1).replace(",", "")) if m else None


def _total(cells: List[Optional[float]]) -> str:
    if not cells or any(c is None for c in cells):
        return ""
    return f"{sum(cells):.2f}".rstrip("0").rstrip(".") or "0"


class HillsboroughCountyScraper(BaseScraper):
    """Fail closed (2026-10-14): the only path is login + reCAPTCHA solve + proxy.

    See docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md. The owner
    approved CAPTCHA solving only for Broward and Lake; proxies and
    authenticated (login) access are not allowed. No source request is made
    until a public, unauthenticated roster contract is proven.
    """

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "HCSO inmate search needs an account login, a solved reCAPTCHA v2 and a "
        "SOCKS/residential proxy fallback (all removed 2026-10-16); CAPTCHA solving is owner-approved only "
        "for Broward/Lake and proxies/login are not allowed, so refuse emission."
    )

    @property
    def county(self) -> str:
        return "Hillsborough"

    def scrape(self) -> List[ArrestRecord]:
        # No source request: login + CAPTCHA + proxy were the only path (all removed).
        logger.warning("Hillsborough: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []

    def _parse_results_table(self, soup):
        records = []
        results_table = soup.find("table", class_="table-striped")
        if not results_table:
            return records
        tbody = results_table.find("tbody") or results_table
        all_rows = tbody.find_all("tr", recursive=False)
        i = 0
        while i < len(all_rows):
            try:
                row = all_rows[i]
                # Skip separator rows
                cls = row.get("class", [])
                if "table-separator" in cls:
                    i += 1
                    continue

                cells = row.find_all("td", recursive=False)
                if len(cells) >= 5:
                    name_link = cells[0].find("a")
                    if name_link:
                        record = self._parse_inmate_block(
                            all_rows, i, cells, name_link
                        )
                        if record:
                            records.append(record)
                        # Skip remaining rows for this inmate block
                        # Structure: name(i) → address(i+1) → dates(i+2) → charges(i+3) → separator(i+4)
                        i += 5
                        continue
                i += 1
            except Exception:
                i += 1
        return records

    def _parse_inmate_block(self, all_rows, i, cells, name_link):
        full_name = name_link.get_text(strip=True)
        first_name, middle_name, last_name = self._parse_name(full_name)
        href = name_link.get("href", "")
        if href and not href.startswith("http"):
            href = "https://webapps.hcso.tampa.fl.us" + href
        booking_number = cells[1].get_text(strip=True)
        agency = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        demo = cells[4].get_text(strip=True)
        demo_parts = [p.strip() for p in demo.split("/")]
        race = demo_parts[0] if len(demo_parts) >= 1 else ""
        sex = demo_parts[1] if len(demo_parts) >= 2 else ""
        dob = demo_parts[3] if len(demo_parts) >= 4 else ""

        # Row i+1: Address
        address = ""
        if i + 1 < len(all_rows):
            for cell in all_rows[i + 1].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ADDRESS:"):
                    address = text.replace("ADDRESS:", "").strip()

        # Row i+2: Release date / code / SOID
        # Status only from a published release date; otherwise "" (never assumed).
        booking_date, arrest_date, status, release_date = "", "", "", ""
        if i + 2 < len(all_rows):
            for cell in all_rows[i + 2].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("RELEASE DATE:"):
                    release = text.replace("RELEASE DATE:", "").strip()
                    if release:
                        status, release_date = "Released", release
                elif text.startswith("ARREST DATE:"):
                    arrest_date = text.replace("ARREST DATE:", "").strip()
                elif text.startswith("BOOKING DATE:"):
                    booking_date = text.replace("BOOKING DATE:", "").strip()

        # Row i+3: Charges table (nested <table>)
        charges_list, bond_cells, case_number = [], [], ""
        if i + 3 < len(all_rows):
            nested = all_rows[i + 3].find("table")
            if nested:
                for cr in nested.find_all("tr"):
                    cc = cr.find_all("td")
                    if len(cc) >= 2:
                        desc = cc[1].get_text(strip=True)
                        if desc and "Charge Type" not in desc and "Charge(s)" not in desc:
                            charges_list.append(desc)
                            # Bond cell per charge: a published amount, else None (unknown).
                            bond_cells.append(_published_amount(cc[4].get_text(strip=True)) if len(cc) >= 5 else None)
                        if len(cc) >= 4 and not case_number:
                            cn = cc[3].get_text(strip=True)
                            if cn and "-" in cn:
                                case_number = cn

        if not booking_number:
            return None

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Arrest_Date=arrest_date,
            Status=status,
            Release_Date=release_date,
            Facility="Falkenburg Road Jail",
            Race=race,
            Sex=sex,
            DOB=dob,
            Address=address,
            Charges=" | ".join(charges_list),
            Bond_Amount=_total(bond_cells),
            Case_Number=case_number,
            Detail_URL=href,
            Agency=agency,
            LastCheckedMode="INITIAL",
            extra_data={"status_source": "published_release_date"} if status else {},
        )

    @staticmethod
    def _parse_name(name_str):
        if not name_str:
            return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            return (
                name_parts[0] if name_parts else "",
                " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
                last_name,
            )
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""
