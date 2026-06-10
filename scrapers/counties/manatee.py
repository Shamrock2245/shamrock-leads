"""
Manatee County Arrest Scraper — Revize CMS via SOCKS Proxy
============================================================
Source: Manatee County Sheriff's Office
URL: https://manatee-sheriff.revize.com/bookings
Method: Playwright + SOCKS5 proxy (office iMac residential IP)

HISTORY:
- v1 (DrissionPage): Chromium headless → Cloudflare challenge timeout
- v2 (Obscura): CF hard-blocked VPS datacenter IP entirely
- v3 (JailTracker): CAPTCHA solved but Blazor WASM crashes with 400
- v4 (Revize + SOCKS): Route through office iMac residential IP —
  Cloudflare doesn't challenge residential IPs. Simple & reliable.
"""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://manatee-sheriff.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
SOCKS_PROXY = "socks5://172.18.0.1:1080"
MAX_PAGES = 20
DETAIL_DELAY_S = 1.0
MAX_BOND_PER_CHARGE = 5_000_000
MAX_BOND_TOTAL = 10_000_000


class ManateeCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Manatee"

    def scrape(self) -> List[ArrestRecord]:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": SOCKS_PROXY},
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            # Phase 1: Collect booking links from roster pages
            booking_links = self._collect_booking_links(page)
            if not booking_links:
                logger.warning("[Manatee] No booking links found")
                return []

            logger.info(f"[Manatee] Found {len(booking_links)} bookings to scrape")

            # Phase 2: Visit each detail page
            records = []
            for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
                if idx % 20 == 0:
                    logger.info(f"[Manatee] Progress: {idx}/{len(booking_links)} ({len(records)} records)")
                try:
                    record = self._extract_detail(page, booking_id, detail_url)
                    if record and record.Full_Name and record.Booking_Number:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"[Manatee] Error on {booking_id}: {e}")
                time.sleep(DETAIL_DELAY_S)

            logger.info(f"[Manatee] Scraped {len(records)} records via SOCKS proxy 🧦")
            return records

        except Exception as e:
            logger.error(f"[Manatee] Fatal error: {e}")
            raise
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass

    def _collect_booking_links(self, page) -> list:
        """Paginate through Revize roster and collect detail URLs."""
        all_links = []
        seen = set()

        for pg in range(1, MAX_PAGES + 1):
            url = BOOKINGS_URL if pg == 1 else f"{BOOKINGS_URL}?page={pg}"
            logger.info(f"[Manatee] Roster page {pg}")

            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # Check for Cloudflare block
            title = (page.title() or "").lower()
            if "just a moment" in title or "attention" in title or "blocked" in title:
                logger.error(f"[Manatee] Cloudflare blocked on page {pg}")
                break

            # Extract booking links
            urls = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                const urls = new Set();
                links.forEach(link => {
                    let href = link.getAttribute('href');
                    if (!href || /\\/bookings\\/?$/i.test(href)) return;
                    if (!href.startsWith('http'))
                        href = 'https://manatee-sheriff.revize.com' + (href.startsWith('/') ? href : '/' + href);
                    urls.add(href);
                });
                return Array.from(urls);
            }""")

            if not urls:
                logger.info(f"[Manatee] No links on page {pg} — end of roster")
                break

            new_count = 0
            for href in urls:
                if href not in seen:
                    seen.add(href)
                    bid = href.split("/bookings/")[-1].split("?")[0].strip("/")
                    if bid:
                        all_links.append((bid, href))
                        new_count += 1

            logger.info(f"[Manatee] Page {pg}: +{new_count} (total: {len(all_links)})")
            if new_count == 0:
                break

        return all_links

    def _extract_detail(self, page, booking_id: str, detail_url: str) -> Optional[ArrestRecord]:
        """Visit a booking detail page and extract structured data."""
        page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1)

        data = page.evaluate("""() => {
            const getText = (label) => {
                const cells = Array.from(document.querySelectorAll('td, th, dt, dd, span, div'));
                for (let i = 0; i < cells.length; i++) {
                    if (cells[i].textContent.trim().toLowerCase().includes(label.toLowerCase())) {
                        const next = cells[i+1] || cells[i].parentElement?.querySelector('td:last-child, dd');
                        if (next) return next.textContent.trim();
                    }
                }
                return '';
            };

            const chargeRows = [];
            const tables = document.querySelectorAll('table');
            tables.forEach(t => {
                const headers = Array.from(t.querySelectorAll('th')).map(h => h.textContent.trim().toLowerCase());
                if (headers.some(h => h.includes('charge') || h.includes('offense') || h.includes('bond'))) {
                    const rows = t.querySelectorAll('tbody tr');
                    rows.forEach(r => {
                        const cells = Array.from(r.querySelectorAll('td')).map(c => c.textContent.trim());
                        if (cells.length >= 2) chargeRows.push(cells);
                    });
                }
            });

            return {
                name: getText('Name') || getText('Inmate') || getText('Defendant'),
                booking: getText('Booking') || getText('Book #'),
                dob: getText('DOB') || getText('Date of Birth') || getText('Birth'),
                gender: getText('Gender') || getText('Sex'),
                race: getText('Race'),
                arrestDate: getText('Arrest Date') || getText('Booked'),
                releaseDate: getText('Release') || getText('Released'),
                facility: getText('Facility') || getText('Housing'),
                charges: chargeRows,
            };
        }""")

        if not data or not data.get("name"):
            return None

        # Parse name
        name = data["name"]
        parts = name.split(",") if "," in name else name.rsplit(" ", 1)
        last_name = parts[0].strip() if parts else name
        first_name = parts[1].strip() if len(parts) > 1 else ""

        # Parse charges and bonds
        charges_list = []
        bond_total = 0.0
        bond_type = ""
        for row in (data.get("charges") or []):
            charge_desc = row[0] if row else ""
            if charge_desc:
                charges_list.append(charge_desc)
            for cell in row:
                amount = self._parse_bond_amount(cell)
                if amount and amount <= MAX_BOND_PER_CHARGE:
                    bond_total += amount
                if any(bt in cell.upper() for bt in ["SURETY", "CASH", "ROR", "NO BOND"]):
                    bond_type = cell.strip()

        bond_total = min(bond_total, MAX_BOND_TOTAL)

        # Parse custody status
        release = data.get("releaseDate", "")
        if "in custody" in release.lower():
            custody = "In Custody"
        elif release and release.lower() not in ["", "n/a"]:
            custody = "Released"
        else:
            custody = "In Custody"

        # Parse dates
        arrest_date = self._parse_date(data.get("arrestDate", ""))
        dob = self._parse_date(data.get("dob", ""))

        return ArrestRecord(
            County="Manatee",
            Booking_Number=data.get("booking", "") or booking_id,
            Full_Name=name,
            First_Name=first_name,
            Last_Name=last_name,
            DOB=dob,
            Gender=data.get("gender", ""),
            Race=data.get("race", ""),
            Arrest_Date=arrest_date,
            Booking_Date=arrest_date,
            Facility=data.get("facility", "") or "Manatee County Jail",
            Charges="; ".join(charges_list) if charges_list else "",
            Bond_Amount=bond_total if bond_total > 0 else None,
            Bond_Type=bond_type or None,
            Custody_Status=custody,
            Source_URL=detail_url,
        )

    @staticmethod
    def _parse_bond_amount(text: str) -> Optional[float]:
        match = re.search(r'\$?([\d,]+\.?\d*)', text.replace(",", ""))
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_date(text: str) -> Optional[str]:
        if not text:
            return None
        for fmt in ["%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m/%d/%y"]:
            try:
                return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return text.strip() if text.strip() else None
