"""
Broward County Arrest Scraper — Multi-Agency Sequential ID Probing.
Source: Broward Sheriff's Office
URL: https://apps.sheriff.org/ArrestSearch/InmateDetail/{ID}
Method: Sequential ID probing via HTTP requests (no browser needed)
"""
import logging, os, re, json, time, urllib.request, urllib.error
from datetime import datetime, timezone
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://apps.sheriff.org"
DETAIL_URL = f"{BASE_URL}/ArrestSearch/InmateDetail"
AGENCY_PREFIXES = {
    23: {"name": "Pompano Beach PD", "active": True, "frontier": 232601027, "rate": 13},
    25: {"name": "Sunrise PD", "active": True, "frontier": 252600276, "rate": 5},
    50: {"name": "BSO Direct", "active": True, "frontier": 502601127, "rate": 23},
    57: {"name": "Fort Lauderdale PD", "active": True, "frontier": 572601255, "rate": 15},
    80: {"name": "Main Jail", "active": False, "frontier": 802600100, "rate": 2},
    90: {"name": "U.S. Marshals Service", "active": True, "frontier": 902600270, "rate": 9},
}
ID_DENSITY = 0.45
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
STATE_DIR = os.path.join(os.path.dirname(__file__), ".state")

class BrowardCountyScraper(BaseScraper):
    DAYS_BACK = 2
    @property
    def county(self) -> str:
        return "Broward"
    def scrape(self) -> List[ArrestRecord]:
        all_records = []
        saved_frontiers = self._load_frontiers()
        for prefix, info in AGENCY_PREFIXES.items():
            if not info["active"]: continue
            start_frontier = saved_frontiers.get(str(prefix), info["frontier"])
            logger.info(f"Broward: prefix {prefix} ({info['name']}) frontier={start_frontier}")
            frontier = self._find_frontier(start_frontier)
            rate = info["rate"]
            ids_per_day = int(rate / ID_DENSITY) if ID_DENSITY > 0 else 30
            scan_range = ids_per_day * self.DAYS_BACK
            scan_start = frontier - scan_range
            records, consecutive_misses = [], 0
            for jms_id in range(scan_start, frontier + 30):
                record = self._fetch_and_parse(jms_id)
                if record: records.append(record); consecutive_misses = 0
                else:
                    consecutive_misses += 1
                    if jms_id > frontier and consecutive_misses > 8: break
                time.sleep(0.15)
            all_records.extend(records)
            if records:
                max_id = max(int(r.Booking_Number) for r in records if r.Booking_Number.isdigit())
                saved_frontiers[str(prefix)] = max_id
        self._save_frontiers(saved_frontiers)
        logger.info(f"Broward: {len(all_records)} total across all agencies")
        return all_records

    def _http_get(self, url):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except: return ""

    def _find_frontier(self, start_id):
        step, current = 100, start_id
        while step > 0:
            html = self._http_get(f"{DETAIL_URL}/{current + step}")
            if html and "<h3" in html and len(html) > 1000:
                current = current + step; step = min(step * 2, 500)
            else:
                if step <= 1: break
                step = step // 2
            time.sleep(0.1)
        return current

    def _fetch_and_parse(self, jms_id):
        html = self._http_get(f"{DETAIL_URL}/{jms_id}")
        if not html or "<h3" not in html or len(html) < 1000: return None
        h3 = re.search(r"<h3>(.*?)</h3>", html)
        if not h3: return None
        name = h3.group(1).strip()
        first_name, middle_name, last_name = self._parse_name(name)
        fields = self._extract_labeled_fields(html)
        race_map = {"W": "White", "B": "Black", "H": "Hispanic", "A": "Asian", "I": "Indian"}
        race = fields.get("Race", ""); race = race_map.get(race, race)
        sex = fields.get("Sex", "")
        dob = fields.get("DOB", "") or fields.get("Date of Birth", "")
        booking_date = fields.get("Booking Date", "")
        agency = fields.get("Arresting Agency", "")
        charges, total_bond = self._extract_charges(html)
        return ArrestRecord(County=self.county, Booking_Number=str(jms_id), Full_Name=name,
            First_Name=first_name, Middle_Name=middle_name, Last_Name=last_name,
            Booking_Date=booking_date, DOB=dob, Race=race, Sex=sex, Status="In Custody",
                        Release_Date="",
            Facility="Broward County Main Jail", Charges=" | ".join(charges) if charges else "",
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Agency=agency, Detail_URL=f"{DETAIL_URL}/{jms_id}", LastCheckedMode="INITIAL")

    @staticmethod
    def _extract_labeled_fields(html):
        fields = {}
        for match in re.finditer(r"<(?:strong|b|label|dt)[^>]*>(.*?)</(?:strong|b|label|dt)>\s*:?\s*</?\\w+[^>]*>?\s*(.*?)(?:<|$)", html, re.IGNORECASE | re.DOTALL):
            key = re.sub(r"<[^>]+>", "", match.group(1)).strip().rstrip(":")
            val = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if key and val: fields[key] = val
        return fields

    @staticmethod
    def _extract_charges(html):
        charges, total_bond = [], 0.0
        for m in re.finditer(r"(?:Statute|Charge|Offense)\s*(?:Description)?[:\s]*([A-Z][A-Za-z0-9\s/.,-]+)", html):
            desc = m.group(1).strip()
            if len(desc) > 3 and desc not in charges: charges.append(desc)
        for m in re.finditer(r"\$\s*([\d,]+(?:\.\d{2})?)", html):
            try: total_bond += float(m.group(1).replace(",", ""))
            except: pass
        return charges, total_bond

    @staticmethod
    def _parse_name(name_str):
        if not name_str: return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1); last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            return name_parts[0] if name_parts else "", " ".join(name_parts[1:]) if len(name_parts) > 1 else "", last_name
        parts = name_str.split(); return parts[0], "", parts[-1] if len(parts) >= 2 else ""

    def _load_frontiers(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        state_file = os.path.join(STATE_DIR, "broward_frontiers.json")
        try:
            with open(state_file) as f: return json.load(f)
        except: return {}

    def _save_frontiers(self, frontiers):
        os.makedirs(STATE_DIR, exist_ok=True)
        state_file = os.path.join(STATE_DIR, "broward_frontiers.json")
        try:
            with open(state_file, "w") as f: json.dump(frontiers, f, indent=2)
        except Exception as e: logger.warning(f"Could not save frontiers: {e}")
