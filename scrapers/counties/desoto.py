"""
DeSoto County Arrest Scraper — DevExpress Grid with DrissionPage.

Source: DeSoto County Sheriff's Office jail roster
URL: https://jail.desotosheriff.org/DCN/inmates
Method: DrissionPage browser automation (Chromium headless)

Architecture:
1. Load roster page → sort by Admit Date descending (newest first)
2. Paginate through DevExpress grid → collect inmate detail links
3. Visit each detail page → extract demographics, charges, bond
4. Date-gated: stops when records fall outside the cutoff window

Note: DeSoto uses a DevExpress ASP.NET grid (same component as many FL jails).
Requires DrissionPage for JavaScript-rendered pagination and AJAX sorting.

Ported from: swfl-arrest-scrapers/python_scrapers/scrapers/desoto_solver.py
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, unquote

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://jail.desotosheriff.org"
INMATES_URL = f"{BASE_URL}/DCN/inmates"
DAYS_BACK = 14
MAX_PAGES = 10
DETAIL_DELAY_S = 1.0


class DeSotoCountyScraper(BaseScraper):
    """DeSoto County (FL) arrest scraper — DevExpress grid via DrissionPage."""

    @property
    def county(self) -> str:
        return "DeSoto"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape DeSoto County via DrissionPage browser automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()
        cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)

        try:
            detail_urls = self._collect_roster_links(page)

            if not detail_urls:
                logger.warning("⚠️ No inmate detail links found on DeSoto roster")
                return []

            logger.info(f"📋 Collected {len(detail_urls)} inmate detail links")

            records: List[ArrestRecord] = []

            for idx, url in enumerate(detail_urls, 1):
                if idx % 10 == 0:
                    logger.info(
                        f"🔍 Progress: {idx}/{len(detail_urls)} "
                        f"({len(records)} records so far)"
                    )

                try:
                    record = self._extract_detail(page, url)

                    if not record:
                        continue

                    if record.Booking_Date:
                        try:
                            for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %I:%M %p"]:
                                try:
                                    book_dt = datetime.strptime(
                                        record.Booking_Date.split()[0], fmt
                                    )
                                    if book_dt < cutoff_date:
                                        logger.info(
                                            f"⏸️ Past cutoff ({record.Booking_Date}), "
                                            f"stopping. Got {len(records)} records."
                                        )
                                        return records
                                    break
                                except ValueError:
                                    continue
                        except Exception:
                            pass

                    if record.Full_Name and record.Booking_Number:
                        records.append(record)

                except Exception as e:
                    logger.warning(f"⚠️ Error on detail page: {e}")

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"✅ Scraped {len(records)} records from DeSoto")
            return records

        except Exception as e:
            logger.error(f"❌ DeSoto scraper fatal error: {e}")
            return []

        finally:
            try:
                page.quit()
            except Exception:
                pass

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    def _collect_roster_links(self, page) -> List[str]:
        logger.info(f"📡 Loading roster: {INMATES_URL}")
        page.get(INMATES_URL)
        time.sleep(3)

        self._sort_by_admit_date(page)

        all_links = self._extract_links_from_page(page)
        logger.info(f"📄 Page 1: {len(all_links)} links")

        page_num = 2
        while page_num <= MAX_PAGES:
            try:
                next_btn = None
                pager_btns = page.eles('css:.dxp-num')
                for btn in pager_btns:
                    if btn.text.strip() == str(page_num):
                        next_btn = btn
                        break

                if not next_btn:
                    try:
                        next_btn = page.ele('#gvInmates_DXPagerBottom_PBN')
                    except Exception:
                        break

                if not next_btn:
                    break

                next_btn.click()
                time.sleep(2)

                new_links = self._extract_links_from_page(page)
                if not new_links:
                    break

                added = 0
                for link in new_links:
                    if link not in all_links:
                        all_links.append(link)
                        added += 1

                logger.info(f"📄 Page {page_num}: {added} new links")

                if added == 0:
                    break

                page_num += 1

            except Exception as e:
                logger.warning(f"⚠️ Pagination error on page {page_num}: {e}")
                break

        return all_links

    def _sort_by_admit_date(self, page):
        try:
            headers = page.eles('tag:th')
            admit_header = None
            for h in headers:
                text = h.text.strip()
                if 'admit date' in text.lower():
                    admit_header = h
                    break

            if not admit_header:
                logger.warning("⚠️ Could not find Admit Date column header, skipping sort")
                return

            logger.debug("Sorting by Admit Date descending...")
            admit_header.click()
            time.sleep(2)
            admit_header.click()
            time.sleep(2)
            logger.debug("✅ Sorted by Admit Date DESC")

        except Exception as e:
            logger.warning(f"⚠️ Sort failed: {e}, continuing without sort")

    @staticmethod
    def _extract_links_from_page(page) -> List[str]:
        html = page.html
        soup = BeautifulSoup(html, 'html.parser')

        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'inmate-details' in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in links:
                    links.append(full_url)
        return links

    def _extract_detail(self, page, detail_url: str) -> Optional[ArrestRecord]:
        page.get(detail_url)
        time.sleep(1.5)

        html = page.html
        soup = BeautifulSoup(html, 'html.parser')

        parsed = urlparse(detail_url)
        qs = parse_qs(parsed.query)
        booking_number = unquote(qs.get('bid', [''])[0])

        full_name = ""
        first_name = ""
        middle_name = ""
        last_name = ""

        name_el = soup.find('h3', class_='header-text')
        if name_el:
            span = name_el.find('span')
            full_name = (span.get_text(strip=True) if span
                         else name_el.get_text(strip=True))
        else:
            header = soup.find(id='HeaderText')
            if header:
                full_name = header.get_text(strip=True)

        if full_name and ',' in full_name:
            parts = full_name.split(',', 1)
            last_name = parts[0].strip()
            first_parts = parts[1].strip().split()
            if first_parts:
                first_name = first_parts[0]
                if len(first_parts) > 1:
                    middle_name = ' '.join(first_parts[1:])

        dob = ""
        sex = ""
        race = ""
        booking_date = ""
        facility = ""
        height = ""
        weight = ""
        address = ""
        city = ""
        state = "FL"
        zipcode = ""
        status = "In Custody"

        detail_table = soup.find(id='tblDetails')
        if detail_table:
            rows = detail_table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 2:
                    label = cells[0].get_text(strip=True).rstrip(':')
                    value = cells[1].get_text(strip=True)
                    if not label or not value:
                        continue
                    if 'Drag a column' in label or 'Change Offset' in label:
                        continue

                    label_lower = label.lower()
                    if 'date of birth' in label_lower or label_lower == 'dob':
                        dob = value
                    elif 'sex' in label_lower or 'gender' in label_lower:
                        sex = value[0].upper() if value else ''
                    elif 'race' in label_lower:
                        race = value
                    elif any(k in label_lower for k in ['booking date', 'date in', 'admit date']):
                        booking_date = value
                    elif 'release date' in label_lower or 'date out' in label_lower:
                        if value and value != 'N/A':
                            status = 'Released'
                    elif 'facility' in label_lower or 'housing' in label_lower:
                        facility = value
                    elif 'height' in label_lower:
                        height = value
                    elif 'weight' in label_lower:
                        weight = value
                    elif 'address' in label_lower:
                        address = value
                        addr_match = re.search(
                            r',\s*(\w[\w\s]*?)\s+([A-Z]{2})\s+(\d{5})', value
                        )
                        if addr_match:
                            city = addr_match.group(1).strip()
                            state = addr_match.group(2)
                            zipcode = addr_match.group(3)

        charges = []
        total_bond = 0.0
        bond_type = ""
        charge_rows = soup.select('[id*="ChargeGrid_DXDataRow"]')

        for cr in charge_rows:
            cells = cr.find_all('td')
            if cells:
                charge_text = cells[0].get_text(strip=True) if len(cells) > 0 else ''
                if charge_text and 'Drag a column' not in charge_text:
                    charges.append(charge_text)

                if len(cells) > 5:
                    bond_text = cells[5].get_text(strip=True)
                    if bond_text:
                        clean = bond_text.replace('$', '').replace(',', '').strip()
                        try:
                            total_bond += float(clean)
                        except ValueError:
                            pass

                if len(cells) > 6 and not bond_type:
                    bond_type = cells[6].get_text(strip=True)

        charges_str = ' | '.join(charges) if charges else ''

        mugshot_url = ""
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'photo' in src.lower() or 'mugshot' in src.lower():
                if not src.startswith('data:'):
                    mugshot_url = urljoin(BASE_URL, src)
                    break

        if not full_name and not booking_number:
            return None

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=dob,
            Booking_Date=booking_date,
            Status=status,
            Facility=facility or "DeSoto County Jail",
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Address=address,
            City=city,
            State=state,
            ZIP=zipcode,
            Mugshot_URL=mugshot_url,
            Charges=charges_str,
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Bond_Type=bond_type,
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )
