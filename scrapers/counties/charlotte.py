"""
Charlotte County Scraper (Revize Platform)
Uses patchright (undetected Playwright) + xvfb-run to bypass Cloudflare Turnstile managed challenges.
"""
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class CharlotteCountyScraper(BaseScraper):
    """
    Scraper for Charlotte County Sheriff's Office.
    Uses patchright via a subprocess wrapper to ensure xvfb-run is used,
    as Cloudflare Turnstile managed challenges require a real display context.
    """

    @property
    def county(self) -> str:
        return "Charlotte"

    def scrape(self) -> List[ArrestRecord]:
        """
        Fetch the roster using a dedicated patchright subprocess script.
        This isolates the browser environment and ensures xvfb-run is applied.
        """
        # Default config for Charlotte
        days_back = 21
        max_pages = 10
        
        logger.info(f"[{self.county}] Starting patchright scraper (days_back={days_back}, max_pages={max_pages})")

        # 1. Write the worker script to a temporary file
        worker_script = "/tmp/charlotte_patchright_worker.py"
        self._write_worker_script(worker_script)

        # 2. Execute the worker script via xvfb-run
        cmd = [
            "xvfb-run",
            "--auto-servernum",
            "--server-args=-screen 0 1920x1080x24",
            sys.executable,
            worker_script,
            str(days_back),
            str(max_pages)
        ]

        logger.info(f"[{self.county}] Executing: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800  # 30 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"[{self.county}] Worker failed with code {result.returncode}")
                logger.error(f"[{self.county}] Worker stderr: {result.stderr}")
                return []
                
            # Parse the JSON output from stdout
            # The worker script prints logs to stderr and only the final JSON array to stdout
            stdout_clean = result.stdout.strip()
            if not stdout_clean:
                logger.warning(f"[{self.county}] Worker returned empty stdout")
                return []
                
            try:
                # Find the JSON array in the output (in case other things leaked into stdout)
                match = re.search(r'\[.*\]', stdout_clean, re.DOTALL)
                if match:
                    raw_records = json.loads(match.group(0))
                else:
                    raw_records = json.loads(stdout_clean)
                    
                logger.info(f"[{self.county}] Worker returned {len(raw_records)} raw records")
                
            except json.JSONDecodeError as e:
                logger.error(f"[{self.county}] Failed to parse worker JSON: {e}")
                logger.error(f"[{self.county}] Raw stdout preview: {stdout_clean[:500]}")
                return []
                
            # Convert raw dicts to ArrestRecord objects
            records = []
            for raw in raw_records:
                try:
                    record = self._convert_to_record(raw)
                    if record:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"[{self.county}] Failed to convert record: {e}")
                    
            logger.info(f"[{self.county}] Successfully converted {len(records)} ArrestRecords")
            return records
            
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.county}] Worker timed out after 30 minutes")
            return []
        except Exception as e:
            logger.error(f"[{self.county}] Error running worker: {e}")
            return []

    def _write_worker_script(self, path: str):
        """Write the standalone patchright worker script."""
        script_content = '''"""
Standalone patchright worker for Charlotte County.
Prints logs to stderr, and final JSON array to stdout.
"""
import json
import sys
import time
from datetime import datetime, timedelta

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def main():
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        eprint("ERROR: patchright not installed. Run: pip install patchright && patchright install chromium")
        sys.exit(1)

    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    cutoff_date = datetime.now() - timedelta(days=days_back)
    eprint(f"Starting Charlotte worker (cutoff: {cutoff_date.strftime('%Y-%m-%d')}, max_pages: {max_pages})")

    base_url = "https://inmates.charlottecountyfl.revize.com"
    bookings_url = f"{base_url}/bookings"
    
    all_raw_records = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # MUST be False for xvfb-run to provide a real display
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )
        
        page = context.new_page()
        
        # 1. Navigate to roster and clear Cloudflare
        eprint(f"Navigating to {bookings_url}...")
        try:
            page.goto(bookings_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            eprint(f"Navigation error: {e}")
            browser.close()
            sys.exit(1)
            
        eprint("Waiting for Cloudflare to clear...")
        cleared = False
        for i in range(60):
            time.sleep(1)
            title = page.title()
            if title and "just a moment" not in title.lower() and "security" not in title.lower():
                cleared = True
                eprint(f"Cloudflare cleared at {i}s! Title: {title}")
                break
                
        if not cleared:
            eprint("FAIL: Cloudflare did not clear in 60s")
            browser.close()
            sys.exit(1)
            
        # 2. Collect booking links across pages
        booking_links = []
        current_page = 1
        
        while current_page <= max_pages:
            eprint(f"Scraping page {current_page}...")
            
            # Wait for table to load
            try:
                page.wait_for_selector("table tbody tr", timeout=10000)
            except:
                eprint("Timeout waiting for table rows")
                break
                
            # Extract links from current page
            page_links = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                    const urls = new Set();
                    links.forEach(link => {
                        let href = link.getAttribute('href');
                        if (!href) return;
                        if (/\\/bookings\\/?$/.test(href)) return;
                        if (!href.startsWith('http')) {
                            href = 'https://inmates.charlottecountyfl.revize.com' + (href.startsWith('/') ? href : '/' + href);
                        }
                        urls.add(href);
                    });
                    return Array.from(urls);
                }
            """)
            
            if not page_links:
                eprint("No links found on page")
                break
                
            new_links = [link for link in page_links if link not in booking_links]
            booking_links.extend(new_links)
            eprint(f"Found {len(new_links)} new links on page {current_page} (Total: {len(booking_links)})")
            
            # Check for next page
            next_btn = page.locator("a.page-link", has_text="Next")
            if next_btn.count() > 0 and next_btn.is_visible():
                try:
                    next_btn.click()
                    time.sleep(2)  # Wait for load
                    current_page += 1
                except Exception as e:
                    eprint(f"Failed to click next page: {e}")
                    break
            else:
                eprint("No more pages")
                break
                
        eprint(f"Total booking links to process: {len(booking_links)}")
        
        # 3. Process each detail page
        for i, detail_url in enumerate(booking_links):
            eprint(f"[{i+1}/{len(booking_links)}] Processing {detail_url}")
            
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)
                
                # Wait for CF if it pops up again
                for _ in range(15):
                    if "just a moment" not in page.title().lower():
                        break
                    time.sleep(1)
                    
                # Extract data via JS
                js_data = page.evaluate("""
                    () => {
                        const result = {};
                        
                        // Label/value pairs
                        document.querySelectorAll('label').forEach(label => {
                            const text = label.textContent.trim().replace(/:$/, '');
                            const forId = label.getAttribute('for');
                            let value = null;
                            if (forId) {
                                const input = document.getElementById(forId);
                                if (input) value = input.value || input.textContent;
                            }
                            if (!value) {
                                const next = label.nextElementSibling;
                                if (next) value = next.textContent || next.value;
                            }
                            if (value && value.trim()) result[text] = value.trim();
                        });
                        
                        // Definition lists
                        document.querySelectorAll('dt').forEach(dt => {
                            const dd = dt.nextElementSibling;
                            if (dd && dd.tagName === 'DD') {
                                result[dt.textContent.trim().replace(/:$/, '')] = dd.textContent.trim();
                            }
                        });
                        
                        // Table cells with headers
                        document.querySelectorAll('table tr').forEach(row => {
                            const cells = row.querySelectorAll('td, th');
                            if (cells.length === 2) {
                                const key = cells[0].textContent.trim().replace(/:$/, '');
                                const val = cells[1].textContent.trim();
                                if (key && val) result[key] = val;
                            }
                        });
                        
                        // Charges table
                        const charges = [];
                        document.querySelectorAll('table').forEach(table => {
                            const headers = Array.from(table.querySelectorAll('th')).map(h => h.textContent.trim());
                            if (headers.some(h => h.includes('Statute') || h.includes('Charge') || h.includes('Desc'))) {
                                table.querySelectorAll('tbody tr').forEach(row => {
                                    const cells = row.querySelectorAll('td');
                                    if (cells.length >= 3) {
                                        charges.push({
                                            date: cells[0] ? cells[0].textContent.trim() : '',
                                            statute: cells[1] ? cells[1].textContent.trim() : '',
                                            desc: cells[2] ? cells[2].textContent.trim() : '',
                                            sec_desc: cells[3] ? cells[3].textContent.trim() : '',
                                            level: cells[4] ? cells[4].textContent.trim() : '',
                                            bond: cells[5] ? cells[5].textContent.trim() : ''
                                        });
                                    }
                                });
                            }
                        });
                        result['__CHARGES'] = charges;
                        
                        // ICE hold
                        result['__HAS_ICE'] = document.body.textContent.includes('ICE HOLD') ||
                                               document.body.textContent.includes('IMMIGRATION DETAINER');
                                               
                        // Mugshot
                        const img = document.querySelector('img[src*="photo"], img[src*="mugshot"], img[src*="image"], img[src*="booking"]');
                        if (img && img.src && !img.src.startsWith('data:')) result['__Mugshot'] = img.src;
                        
                        return result;
                    }
                """)
                
                if js_data:
                    js_data['__Detail_URL'] = detail_url
                    js_data['__Booking_ID'] = detail_url.split("/bookings/")[-1].split("?")[0].strip()
                    all_raw_records.append(js_data)
                    eprint(f"  -> Success: {js_data.get('First Name', '')} {js_data.get('Last Name', '')}")
                else:
                    eprint("  -> Failed to extract data")
                    
            except Exception as e:
                eprint(f"  -> Error processing detail page: {e}")
                continue
                
        browser.close()
        
    # Print final JSON array to stdout
    print(json.dumps(all_raw_records))

if __name__ == "__main__":
    main()
'''
        with open(path, "w") as f:
            f.write(script_content)
        os.chmod(path, 0o755)

    def _convert_to_record(self, js_data: dict) -> Optional[ArrestRecord]:
        """Convert raw JS extraction dict to ArrestRecord."""
        if not js_data:
            return None

        booking_id = js_data.get("__Booking_ID", "")
        detail_url = js_data.get("__Detail_URL", "")

        first_name = self._clean(js_data.get("First Name", ""))
        last_name = self._clean(js_data.get("Last Name", ""))
        middle_name = self._clean(js_data.get("Middle Name", ""))
        
        full_name = ""
        if last_name and first_name:
            full_name = f"{last_name}, {first_name}"
            if middle_name:
                full_name += f" {middle_name}"

        # Parse charges & bond
        charges_list = []
        total_bond = 0.0
        booking_date = ""
        
        for entry in js_data.get("__CHARGES", []):
            desc = self._clean(entry.get("desc", ""))
            statute = self._clean(entry.get("statute", ""))
            sec_desc = self._clean(entry.get("sec_desc", ""))
            bond_str = self._clean(entry.get("bond", "0"))
            arr_date = self._clean(entry.get("date", ""))
            
            if not booking_date and arr_date:
                booking_date = arr_date
                
            charge_text = self._clean_charge_text(desc)
            if sec_desc and sec_desc != "A/W":
                charge_text += f" ({sec_desc})"
            if statute:
                charge_text = f"{statute} - {charge_text}"
                
            if charge_text:
                charges_list.append(charge_text)
                
            try:
                total_bond += float(bond_str.replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                pass

        # ICE hold
        if js_data.get("__HAS_ICE"):
            charges_list.insert(0, "ICE HOLD")

        # Booking date fallback
        if not booking_date and js_data.get("Booking Date"):
            bd = self._clean(js_data["Booking Date"])
            if len(bd) > 5:
                booking_date = bd

        status = self._clean(js_data.get("Status", "In Custody"))
        charges_str = " | ".join(charges_list) if charges_list else ""

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(js_data.get("Date of Birth", "")),
            Booking_Date=booking_date,
            Status=status,
            Facility="Charlotte County Jail",
            Race=self._clean(js_data.get("Race", "")),
            Sex=self._clean(js_data.get("Sex", js_data.get("Gender", ""))),
            Height=self._clean(js_data.get("Height", "")),
            Weight=self._clean(js_data.get("Weight", "")),
            Address=self._clean(js_data.get("Address", "")),
            City=self._clean(js_data.get("City", "")),
            State=self._clean(js_data.get("State", "FL")),
            ZIP=self._clean(js_data.get("Zip Code", "")),
            Mugshot_URL=js_data.get("__Mugshot", ""),
            Charges=charges_str,
            Bond_Amount=str(total_bond) if total_bond > 0 else "",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _clean(text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _clean_charge_text(raw_charge: str) -> str:
        """Clean charge text to extract human-readable description."""
        if not raw_charge:
            return ""
        text = re.sub(r"^(New Charge:|Weekender:)\s*", "", raw_charge, flags=re.IGNORECASE)
        match = re.search(r"[\d.]+[a-z]*\s*-\s*([^(]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if "(" in text:
            description = text.split("(")[0].strip()
            description = re.sub(r"^[\d.]+[a-z]*\s*-\s*", "", description)
            return description.strip()
        return text.strip()
