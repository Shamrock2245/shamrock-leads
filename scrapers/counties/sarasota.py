"""
Sarasota County Arrest Scraper — Revize CMS with patchright (undetected Playwright).
Source: Sarasota County Sheriff's Office via Revize-hosted bookings
URL: https://sarasotasheriff.org/arrest-reports/index.php (iframe: cms.revize.com/revize/apps/sarasota/)
Method: patchright subprocess via xvfb-run (same approach as Charlotte County)
Reason for patchright: cms.revize.com returns 403 to datacenter IPs; navigating via the
  parent sarasotasheriff.org page with a real browser context bypasses the block.
Architecture (2-Phase):
1. Navigate to parent page -> enter Revize iframe -> collect booking detail URLs (paginated by date)
2. Visit each detail URL -> extract structured arrest data
"""
import json
import logging
import re
import subprocess
import sys
import time
import datetime as dt
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PARENT_URL = "https://sarasotasheriff.org/arrest-reports/index.php"
BASE_URL = "https://cms.revize.com/revize/apps/sarasota/"
DAYS_BACK = 3
MAX_PAGES_PER_DATE = 30
WORKER_TIMEOUT = 600  # 10 minutes max for the patchright subprocess


class SarasotaCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Sarasota"

    # ── Main scrape entry point ───────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        worker_script = "/tmp/sarasota_patchright_worker.py"
        self._write_worker_script(worker_script)

        cmd = ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24",
               sys.executable, worker_script, str(DAYS_BACK), str(MAX_PAGES_PER_DATE)]

        logger.info(f"[{self.county}] Starting patchright worker (days_back={DAYS_BACK})")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=WORKER_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.county}] Worker timed out after {WORKER_TIMEOUT}s")
            return []
        except FileNotFoundError:
            logger.warning(f"[{self.county}] xvfb-run not found, trying direct patchright")
            cmd = [sys.executable, worker_script, str(DAYS_BACK), str(MAX_PAGES_PER_DATE)]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=WORKER_TIMEOUT)
            except subprocess.TimeoutExpired:
                logger.error(f"[{self.county}] Worker timed out")
                return []

        if result.stderr:
            for line in result.stderr.strip().split("\n")[-30:]:
                logger.debug(f"[sarasota-worker] {line}")

        if result.returncode != 0:
            logger.error(f"[{self.county}] Worker exited {result.returncode}")
            return []

        raw_records = []
        try:
            raw_records = json.loads(result.stdout.strip())
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[{self.county}] JSON parse error: {e} | stdout[:200]: {result.stdout[:200]}")
            return []

        logger.info(f"[{self.county}] Worker returned {len(raw_records)} raw records")
        records = []
        for raw in raw_records:
            rec = self._build_record(raw)
            if rec and rec.Full_Name:
                records.append(rec)

        logger.info(f"[{self.county}] Parsed {len(records)} valid ArrestRecords")
        return records

    # ── Build ArrestRecord from raw worker dict ───────────────────────────────
    def _build_record(self, raw: dict) -> Optional[ArrestRecord]:
        try:
            full_name = raw.get("full_name", "").strip()
            if not full_name:
                return None

            first_name = raw.get("first_name", "")
            last_name = raw.get("last_name", "")
            if not first_name and not last_name and "," in full_name:
                parts = full_name.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()

            charges_list = raw.get("charges", [])
            charges_str = " | ".join(c for c in charges_list if c) if charges_list else ""

            total_bond = 0.0
            for c in raw.get("charges_raw", []):
                bond_str = str(c.get("bond", "0")).replace("$", "").replace(",", "").strip()
                try:
                    total_bond += float(bond_str)
                except ValueError:
                    pass
            if total_bond == 0 and raw.get("total_bond"):
                try:
                    total_bond = float(str(raw["total_bond"]).replace("$", "").replace(",", ""))
                except ValueError:
                    pass

            return ArrestRecord(
                County=self.county,
                Booking_Number=raw.get("booking_id", ""),
                Full_Name=full_name,
                First_Name=first_name,
                Last_Name=last_name,
                DOB=raw.get("dob", ""),
                Booking_Date=raw.get("booking_date", ""),
                Status="In Custody",
                Release_Date="",
                Facility=raw.get("facility", "Sarasota County Jail"),
                Agency=raw.get("agency", ""),
                Race=raw.get("race", ""),
                Sex=raw.get("sex", ""),
                Height=raw.get("height", ""),
                Weight=raw.get("weight", ""),
                Address=raw.get("address", ""),
                City=raw.get("city", ""),
                State=raw.get("state", "FL"),
                ZIP=raw.get("zip", ""),
                Mugshot_URL=raw.get("mugshot_url", ""),
                Charges=charges_str,
                Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                Bond_Paid="NO",
                Detail_URL=raw.get("detail_url", ""),
                LastCheckedMode="INITIAL",
            )
        except Exception as e:
            logger.warning(f"[{self.county}] _build_record error: {e}")
            return None

    # ── Patchright worker script ──────────────────────────────────────────────
    @staticmethod
    def _write_worker_script(path: str):
        script = r'''"""
Standalone patchright worker for Sarasota County.
Navigates via the parent sarasotasheriff.org page to bypass cms.revize.com 403.
Prints logs to stderr, final JSON array to stdout.
"""
import json
import sys
import time
import re
from datetime import datetime, timedelta

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def clean_charge(raw):
    if not raw:
        return ""
    text = re.sub(r"^(New Charge:|Weekender:)\s*", "", raw, flags=re.IGNORECASE)
    m = re.search(r"[\d.]+[a-z]*\s*-\s*([^(]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if "(" in text:
        desc = text.split("(")[0].strip()
        desc = re.sub(r"^[\d.]+[a-z]*\s*-\s*", "", desc)
        return desc.strip()
    return text.strip()

def main():
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        eprint("ERROR: patchright not installed.")
        sys.exit(1)

    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    parent_url = "https://sarasotasheriff.org/arrest-reports/index.php"
    base_url = "https://cms.revize.com/revize/apps/sarasota/"

    today = datetime.now()
    target_dates = []
    for i in range(days_back):
        d = today - timedelta(days=i)
        target_dates.append(d.strftime("%m/%d/%Y"))

    eprint(f"Sarasota worker starting. Target dates: {target_dates}")
    all_raw_records = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--window-size=1920,1080", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = context.new_page()

        # Navigate to parent page first to establish Referer/cookie context
        eprint(f"Navigating to parent: {parent_url}")
        try:
            page.goto(parent_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            eprint(f"Parent nav error: {e}")
            browser.close()
            sys.exit(1)

        for i in range(30):
            time.sleep(1)
            title = page.title()
            if title and "just a moment" not in title.lower():
                eprint(f"Parent page ready: {title}")
                break

        # Collect booking links by date
        booking_links = []

        for date_str in target_dates:
            search_url = f"{base_url}personSearch.php?type=date&date={date_str}"
            eprint(f"Searching date: {date_str}")
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                eprint(f"Search nav error for {date_str}: {e}")
                continue

            for i in range(30):
                time.sleep(1)
                title = page.title()
                if title and "just a moment" not in title.lower() and "403" not in title:
                    break

            page_num = 1
            while page_num <= max_pages:
                try:
                    page.wait_for_selector(
                        "a[href*='viewInmate.php'], a[href*='booking.php'], a[href*='pinSearch.php']",
                        timeout=8000
                    )
                except Exception:
                    eprint(f"  No links on page {page_num} for {date_str}")
                    break

                links = page.query_selector_all("a[href*='viewInmate.php'], a[href*='booking.php']")
                existing_urls = {b["detail_url"] for b in booking_links}
                new_count = 0
                for link in links:
                    href = link.get_attribute("href") or ""
                    if not href.startswith("http"):
                        href = base_url + href.lstrip("/")
                    if href not in existing_urls:
                        bid = href.split("id=")[-1].split("&")[0] if "id=" in href else ""
                        booking_links.append({"booking_id": bid, "detail_url": href})
                        existing_urls.add(href)
                        new_count += 1

                eprint(f"  Page {page_num}: +{new_count} bookings (total: {len(booking_links)})")

                next_link = page.query_selector(f"a[href*='page={page_num + 1}']")
                if not next_link:
                    break
                try:
                    next_link.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    time.sleep(1)
                except Exception:
                    break
                page_num += 1

        eprint(f"Phase 1 complete: {len(booking_links)} booking URLs")

        if not booking_links:
            eprint("No bookings found")
            browser.close()
            print(json.dumps([]))
            return

        # Phase 2: Extract detail for each booking
        for idx, b in enumerate(booking_links, 1):
            detail_url = b["detail_url"]
            booking_id = b["booking_id"]
            eprint(f"[{idx}/{len(booking_links)}] {detail_url}")
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(1.5)

                for i in range(20):
                    title = page.title()
                    if title and "just a moment" not in title.lower():
                        break
                    time.sleep(1)

                raw = page.evaluate("""
                    () => {
                        const result = {};
                        const h1 = document.querySelector('h1.page-title, h1');
                        if (h1) {
                            let name = h1.textContent.split('Print')[0].trim();
                            result.full_name = name;
                            if (name.includes(',')) {
                                const parts = name.split(',');
                                result.last_name = parts[0].trim();
                                result.first_name = parts[1].trim();
                            }
                        }
                        const fieldMap = {
                            'dob': 'dob', 'date of birth': 'dob',
                            'race': 'race', 'sex': 'sex', 'gender': 'sex',
                            'height': 'height', 'weight': 'weight',
                            'address': 'address', 'city': 'city', 'state': 'state',
                            'zip code': 'zip', 'zip': 'zip',
                            'facility': 'facility', 'agency': 'agency',
                            'arrest date': 'booking_date', 'arrested': 'booking_date',
                            'date arrested': 'booking_date', 'booking date': 'booking_date',
                            'intake date': 'booking_date',
                        };
                        document.querySelectorAll('div.text-right').forEach(ld => {
                            const key = ld.textContent.replace(':', '').trim().toLowerCase();
                            const mapped = fieldMap[key];
                            if (mapped) {
                                const next = ld.nextElementSibling;
                                if (next) {
                                    const val = next.textContent.trim();
                                    if (val && !result[mapped]) result[mapped] = val;
                                }
                            }
                        });
                        const chargesRaw = [];
                        let totalBond = 0;
                        document.querySelectorAll('#data-table tr').forEach(row => {
                            const cells = row.querySelectorAll('td');
                            if (cells.length > 4) {
                                const desc = cells[1] ? cells[1].textContent.trim() : '';
                                const bondStr = cells[4] ? cells[4].textContent.replace(/[$,]/g,'').trim() : '0';
                                const bond = parseFloat(bondStr) || 0;
                                if (desc) { chargesRaw.push({desc, bond}); totalBond += bond; }
                            }
                        });
                        result.charges_raw = chargesRaw;
                        result.total_bond = totalBond;
                        const img = document.querySelector('.mug img, img[alt*="mugshot"], img[src*="photo"]');
                        if (img && img.src && !img.src.startsWith('data:')) result.mugshot_url = img.src;
                        return result;
                    }
                """)

                if raw and raw.get("full_name"):
                    raw["booking_id"] = booking_id
                    raw["detail_url"] = detail_url
                    raw["charges"] = [clean_charge(c.get("desc", "")) for c in raw.get("charges_raw", [])]
                    all_raw_records.append(raw)
                    eprint(f"  -> {raw.get('full_name','?')} bond=${raw.get('total_bond',0)}")
                else:
                    eprint(f"  -> No data extracted")

            except Exception as e:
                eprint(f"  -> Error: {e}")
                continue

        browser.close()

    eprint(f"Worker complete: {len(all_raw_records)} records")
    print(json.dumps(all_raw_records))

if __name__ == "__main__":
    main()
'''
        with open(path, "w") as f:
            f.write(script)

    # ── FirstAppearanceWatcher hook ───────────────────────────────────────────
    def _fetch_single_booking(self, booking_id: str, detail_url: str):
        """
        Re-fetch a single Sarasota County booking via patchright.
        Returns None on any failure (watcher falls back to generic HTTP).
        """
        if not booking_id and not detail_url:
            return None
        if not detail_url:
            detail_url = f"{BASE_URL}booking.php?id={booking_id}"

        worker_script = "/tmp/sarasota_single_patchright.py"
        single_script = f"""
import json, sys, time
from patchright.sync_api import sync_playwright
detail_url = {json.dumps(detail_url)}
booking_id = {json.dumps(booking_id)}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
    context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    page = context.new_page()
    page.goto("https://sarasotasheriff.org/arrest-reports/index.php", wait_until="domcontentloaded", timeout=20000)
    time.sleep(2)
    page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
    time.sleep(2)
    raw = page.evaluate(\"\"\"() => {{
        const r = {{}};
        const h1 = document.querySelector('h1.page-title,h1');
        if (h1) r.full_name = h1.textContent.split('Print')[0].trim();
        let bond = 0;
        document.querySelectorAll('#data-table tr').forEach(row => {{
            const cells = row.querySelectorAll('td');
            if (cells.length > 4) {{
                const b = parseFloat((cells[4].textContent||'0').replace(/[$,]/g,'')) || 0;
                bond += b;
            }}
        }});
        r.total_bond = bond;
        r.booking_id = '{booking_id}';
        r.detail_url = detail_url;
        return r;
    }}\"\"\")
    browser.close()
    print(json.dumps(raw or {{}}))
"""
        try:
            with open(worker_script, "w") as f:
                f.write(single_script)
            cmd = ["xvfb-run", "--auto-servernum", sys.executable, worker_script]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and result.stdout.strip():
                raw = json.loads(result.stdout.strip())
                return self._build_record(raw) if raw.get("full_name") else None
        except Exception as e:
            logger.warning(f"[{self.county}] _fetch_single_booking error: {e}")
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _clean_charge_text(raw_charge):
        if not raw_charge:
            return ""
        text = re.sub(r"^(New Charge:|Weekender:)\s*", "", raw_charge, flags=re.IGNORECASE)
        match = re.search(r"[\d.]+[a-z]*\s*-\s*([^(]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if "(" in text:
            desc = text.split("(")[0].strip()
            desc = re.sub(r"^[\d.]+[a-z]*\s*-\s*", "", desc)
            return desc.strip()
        return text.strip()
