"""
Duval County (Jacksonville) Arrest Scraper — API Interception.
Source: Jacksonville Sheriff's Office
URL: https://inmatesearch.jaxsheriff.org/
Method: DrissionPage browser — intercept Angular SPA internal API traffic
"""
import logging, json, re, time
from datetime import datetime, timezone
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://inmatesearch.jaxsheriff.org/"

class DuvalCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Duval"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed"); return []

        page = self._setup_browser()
        all_records = []
        api_responses = []

        try:
            page.listen.start("json")
            page.get(BASE_URL)
            time.sleep(5)
            for attempt in range(15):
                title = page.title or ""
                if any(kw in title.lower() for kw in ["just a moment", "security", "checking"]):
                    time.sleep(3)
                else:
                    break
            time.sleep(3)
            try:
                search_input = page.ele("tag:input@@type=text", timeout=5)
                if search_input:
                    search_input.input("a"); time.sleep(1)
                search_btn = page.ele("tag:button@@text():Search", timeout=3)
                if search_btn:
                    search_btn.click(); time.sleep(5)
            except Exception: pass
            packets = page.listen.steps(timeout=15)
            for packet in packets:
                try:
                    if hasattr(packet, "response") and packet.response:
                        body = packet.response.body
                        if isinstance(body, (dict, list)):
                            api_responses.append(body)
                        elif isinstance(body, str) and body.strip().startswith(("{","[")):
                            api_responses.append(json.loads(body))
                except Exception: pass
            for data in api_responses:
                records = self._extract_from_api(data)
                all_records.extend(records)
            if not all_records:
                all_records = self._parse_dom(page)
            logger.info(f"Duval: {len(all_records)} records")
            return all_records
        except Exception as e:
            logger.error(f"Duval fatal: {e}"); return []
        finally:
            try: page.listen.stop(); page.quit()
            except: pass

    def _extract_from_api(self, data) -> List[ArrestRecord]:
        records = []
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ["data","results","inmates","entries","items","records","bookings"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]; break
        for entry in entries:
            if not isinstance(entry, dict): continue
            full_name = ""
            for key in ["name","full_name","fullName","inmateName","defendant_name","offenderName"]:
                if key in entry: full_name = str(entry[key]).strip(); break
            if not full_name:
                fn = entry.get("firstName", entry.get("first_name", ""))
                ln = entry.get("lastName", entry.get("last_name", ""))
                if fn and ln: full_name = f"{ln}, {fn}"
            booking_number = ""
            for key in ["bookingNumber","booking_number","id","inmateId","booking_id","jacketNumber"]:
                if key in entry: booking_number = str(entry[key]).strip(); break
            if not full_name and not booking_number: continue
            first_name, middle_name, last_name = self._parse_name(full_name)
            dob = ""
            for key in ["dob","dateOfBirth","date_of_birth","DOB"]:
                if key in entry: dob = str(entry[key]).strip(); break
            bond_amount = ""
            for key in ["bond","bondAmount","bond_amount","totalBond"]:
                if key in entry: bond_amount = str(entry[key]).strip(); break
            charges = ""
            for key in ["charges","charge","offense","offenses"]:
                val = entry.get(key)
                if val:
                    if isinstance(val, list):
                        descs = []
                        for c in val:
                            if isinstance(c, dict):
                                d = c.get("description", c.get("charge", c.get("offense", "")))
                                if d: descs.append(str(d))
                            else: descs.append(str(c))
                        charges = " | ".join(descs)
                    else: charges = str(val)
                    break
            booking_date = ""
            for key in ["bookingDate","booking_date","arrestDate","arrest_date","date"]:
                if key in entry: booking_date = str(entry[key]).strip(); break
            race = entry.get("race", entry.get("Race", ""))
            sex = entry.get("sex", entry.get("gender", entry.get("Sex", "")))
            records.append(ArrestRecord(County=self.county, Booking_Number=booking_number,
                Full_Name=full_name, First_Name=first_name, Middle_Name=middle_name, Last_Name=last_name,
                Booking_Date=booking_date, DOB=dob, Race=race, Sex=sex, Charges=charges,
                Bond_Amount=bond_amount, Status="In Custody", Facility="Duval County Pre-Trial Detention Facility",
                LastCheckedMode="INITIAL"))
        return records

    def _parse_dom(self, page) -> List[ArrestRecord]:
        records = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.html, "html.parser")
            for row in soup.select("table tr, .inmate-row, .result-row"):
                cells = row.find_all(["td","span","div"])
                if len(cells) < 2: continue
                text = row.get_text(" ", strip=True)
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match: continue
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                booking_match = re.search(r"\b(\d{4,})\b", text)
                bond_match = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
                records.append(ArrestRecord(County=self.county,
                    Booking_Number=booking_match.group(1) if booking_match else "",
                    Full_Name=full_name, First_Name=first_name, Middle_Name=middle_name, Last_Name=last_name,
                    Bond_Amount=bond_match.group(1).replace(",","") if bond_match else "0",
                    Status="In Custody", Facility="Duval County Pre-Trial Detention Facility", LastCheckedMode="INITIAL"))
        except Exception as e:
            logger.warning(f"DOM parse error: {e}")
        return records

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.auto_port(); co.headless(True)
        co.set_argument("--no-sandbox"); co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu"); co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _parse_name(name_str):
        if not name_str: return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1); last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            return first_name, middle_name, last_name
        parts = name_str.split(); return parts[0], "", parts[-1] if len(parts) >= 2 else ""
