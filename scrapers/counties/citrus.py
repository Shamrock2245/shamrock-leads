"""
Citrus County Arrest Scraper — PDF Roster via DrissionPage
Source: Citrus County Sheriff's Office
URL: https://www.sheriffcitrus.org/public_info/recent_arrest.php
Method: DrissionPage (bypasses 403) → extract PDF URL from iframe → download → pdfplumber parse
Note: Server returns 403 to plain requests; DrissionPage with real browser headers bypasses this.
Proven pattern: swfl-arrest-scrapers/counties/citrus/solver.py
"""
import logging
import re
import io
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sheriffcitrus.org"
PAGE_URL = f"{BASE_URL}/public_info/recent_arrest.php"
FACILITY = "Citrus County Detention Facility"


class CitrusCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Citrus"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            logger.error("DrissionPage not installed")
            return []

        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed — install with: pip install pdfplumber")
            pdfplumber = None

        opts = self._get_browser_options()

        page = None
        pdf_url = None

        try:
            page = ChromiumPage(addr_or_opts=opts)
            page.get(PAGE_URL)
            page.wait(3)

            # Look for iframe with PDF
            iframes = page.eles("tag:iframe")
            for iframe in iframes:
                src = iframe.attr("src") or ""
                if ".pdf" in src.lower() or "arrest" in src.lower():
                    pdf_url = src if src.startswith("http") else BASE_URL + src
                    break

            # Also look for embed/object tags
            if not pdf_url:
                for tag in ["embed", "object"]:
                    els = page.eles(f"tag:{tag}")
                    for el in els:
                        src = el.attr("src") or el.attr("data") or ""
                        if ".pdf" in src.lower():
                            pdf_url = src if src.startswith("http") else BASE_URL + src
                            break
                    if pdf_url:
                        break

            # Look for direct PDF links
            if not pdf_url:
                links = page.eles("tag:a")
                for link in links:
                    href = link.attr("href") or ""
                    if ".pdf" in href.lower():
                        pdf_url = href if href.startswith("http") else BASE_URL + href
                        break

            # Try to get page source and parse with BeautifulSoup
            if not pdf_url:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.html, "html.parser")
                for iframe in soup.find_all("iframe"):
                    src = iframe.get("src", "")
                    if src:
                        pdf_url = src if src.startswith("http") else BASE_URL + src
                        break

        except Exception as e:
            logger.error(f"Citrus: DrissionPage error: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

        if not pdf_url:
            logger.warning("Citrus: could not find PDF URL")
            return []

        logger.info(f"Citrus: found PDF at {pdf_url}")

        # Download and parse the PDF
        if not pdfplumber:
            logger.warning("Citrus: pdfplumber not available, cannot parse PDF")
            return []

        try:
            import requests
            resp = requests.get(pdf_url, timeout=60, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Referer": PAGE_URL,
            })
            resp.raise_for_status()
            pdf_bytes = resp.content
        except Exception as e:
            logger.error(f"Citrus: failed to download PDF: {e}")
            return []

        return self._parse_pdf(pdf_bytes)

    def _parse_pdf(self, pdf_bytes: bytes) -> List[ArrestRecord]:
        """Parse arrest records from the Citrus County PDF roster."""
        try:
            import pdfplumber
        except ImportError:
            return []

        records = []
        seen = set()

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    # Parse records from text
                    batch = self._parse_pdf_text(text, seen)
                    records.extend(batch)
        except Exception as e:
            logger.error(f"Citrus: PDF parse error: {e}")

        logger.info(f"Citrus: {len(records)} records from PDF")
        return records

    def _parse_pdf_text(self, text: str, seen: set) -> List[ArrestRecord]:
        """
        Parse individual arrest records from PDF page text.
        Citrus PDF format (Nitro Pro 13):
          NAME: LAST, FIRST MIDDLE
          Booking #: XXXXXXX  Date: MM/DD/YYYY
          Charges: ...
          Bond: $X,XXX.XX
        """
        records = []
        if not text:
            return records

        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Pattern: look for lines that start with a name (LAST, FIRST format)
        # followed by booking info
        i = 0
        while i < len(lines):
            line = lines[i]

            # Detect name line: "LASTNAME, FIRSTNAME" or "LASTNAME, FIRSTNAME MIDDLE"
            name_match = re.match(r"^([A-Z][A-Z\s\-\']+),\s+([A-Z][A-Z\s\-\']+)$", line)
            if name_match:
                last_name = name_match.group(1).strip()
                first_middle = name_match.group(2).strip()
                parts = first_middle.split()
                first_name = parts[0] if parts else ""
                middle_name = " ".join(parts[1:]) if len(parts) > 1 else ""
                full_name = f"{last_name}, {first_middle}"

                # Look ahead for booking info
                booking_num = ""
                booking_date = ""
                charges_parts = []
                bond_raw = "0"
                race = ""
                sex = ""
                dob = ""

                j = i + 1
                while j < min(i + 15, len(lines)):
                    next_line = lines[j]

                    # Booking number
                    bk_match = re.search(r"(?:Booking\s*#?:?\s*|BK\s*)([A-Z0-9\-]+)", next_line, re.I)
                    if bk_match:
                        booking_num = bk_match.group(1)

                    # Date
                    dt_match = re.search(r"(?:Date:?\s*)(\d{1,2}/\d{1,2}/\d{2,4})", next_line, re.I)
                    if dt_match:
                        booking_date = dt_match.group(1)

                    # Bond
                    bond_match = re.search(r"(?:Bond:?\s*)\$?([\d,]+(?:\.\d{2})?)", next_line, re.I)
                    if bond_match:
                        bond_raw = bond_match.group(1)

                    # Race/Sex
                    rs_match = re.search(r"\b([BWHAOI])/([MF])\b", next_line)
                    if rs_match:
                        race = rs_match.group(1)
                        sex = rs_match.group(2)

                    # DOB
                    dob_match = re.search(r"(?:DOB:?\s*|Born:?\s*)(\d{1,2}/\d{1,2}/\d{2,4})", next_line, re.I)
                    if dob_match:
                        dob = dob_match.group(1)

                    # Charges (collect multi-line)
                    if re.search(r"(?:Charge|Count|Statute|F\.S\.|§)", next_line, re.I):
                        charges_parts.append(next_line)

                    # Stop if we hit another name
                    if re.match(r"^[A-Z][A-Z\s\-\']+,\s+[A-Z][A-Z\s\-\']+$", next_line) and j > i + 2:
                        break

                    j += 1

                charges = "; ".join(charges_parts) if charges_parts else ""
                key = (full_name, booking_num)
                if key not in seen and (full_name or booking_num):
                    seen.add(key)
                    bond_amount = self._parse_bond(bond_raw)
                    records.append(ArrestRecord(
                        County=self.county,
                        Booking_Number=booking_num,
                        Full_Name=full_name,
                        First_Name=first_name,
                        Middle_Name=middle_name,
                        Last_Name=last_name,
                        DOB=dob,
                        Booking_Date=booking_date,
                        Status="In Custody",
                        Facility=FACILITY,
                        Race=race,
                        Sex=sex,
                        Charges=charges,
                        Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                        LastCheckedMode="INITIAL",
                    ))

            i += 1

        return records

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
