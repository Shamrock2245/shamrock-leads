import logging
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ircsheriff.org"
FACILITY = "Indian River County Jail"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

def _pn(n):
    if not n:
        return "", "", ""
    n = " ".join(n.strip().split())
    if "," in n:
        p = n.split(",", 1)
        l = p[0].strip()
        fm = p[1].strip().split()
        return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
    p = n.split()
    return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")

def _parse_bond(bond_str):
    if not bond_str:
        return 0.0
    cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
    if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
        return 0.0
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0

def _fetch_single_booking(booking_id: str, detail_url: str):
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False
    
    try:
        resp = session.get(detail_url, timeout=30)
        if resp.status_code != 200 or len(resp.text) < 1000:
            print(f"Error HTTP {resp.status_code}")
            return None
            
        soup = BeautifulSoup(resp.text, "html.parser")
        container = soup.find("div", class_="col-lg-12")
        if not container:
            print("Main container col-lg-12 not found")
            return None
            
        tables = container.find_all("table")
        if len(tables) < 2:
            print("Tables missing")
            return None
            
        # Demographics (Table 0)
        demographics = {}
        for row in tables[0].find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                k = cells[0].get_text(strip=True)
                v = cells[1].get_text(strip=True)
                demographics[k] = v
                
        # Booking Info (Table 1)
        booking_info = {}
        for row in tables[1].find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                k = cells[0].get_text(strip=True)
                v = cells[1].get_text(strip=True)
                booking_info[k] = v
                
        # Extract demographics
        full_name = demographics.get("Name", "")
        dob_raw = demographics.get("Date of Birth", "")
        dob = ""
        if dob_raw:
            dob_match = re.search(r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})", dob_raw)
            if dob_match:
                try:
                    dt = datetime.strptime(dob_match.group(1), "%b %d, %Y")
                    dob = dt.strftime("%m/%d/%Y")
                except Exception as e:
                    print("DOB err:", e)
                    
        race = demographics.get("Race", "")
        sex = demographics.get("Sex", "")
        height = demographics.get("Height", "")
        weight = demographics.get("Weight", "")
        address = demographics.get("Address", "")
        if address:
            address = " ".join(address.split())
            
        # Extract booking info
        booking_date_raw = booking_info.get("Booking Date", "")
        arrest_date_raw = booking_info.get("Arrest Date", "")
        arrest_location = booking_info.get("Arrest Location", "")
        if arrest_location:
            arrest_location = " ".join(arrest_location.split())
        agency = booking_info.get("Arresting Agency", "")
        booking_number = booking_info.get("Booking Number", booking_id)
        case_number = booking_info.get("Case Number", "")
        
        # Extract and parse bond amount and type
        bond_val_str = booking_info.get("Bond", "0")
        bond_raw = bond_val_str
        bond_type = ""
        
        if "no bond" in bond_val_str.lower():
            bond_amount = 0.0
            bond_type = "NO BOND"
            bond_raw = "0"
        else:
            bond_amount = _parse_bond(bond_val_str)
            if bond_amount == 0.0 and bond_val_str:
                cleaned = re.sub(r"[$,\s]", "", bond_val_str.upper())
                if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
                    bond_type = "NO BOND"
            
        # Extract Charges
        charge_cards = container.find_all("div", class_="card")
        charges_list = []
        for card in charge_cards:
            header = card.find("div", class_="card-header")
            if header:
                charge_desc = header.get_text(strip=True)
                if charge_desc:
                    charges_list.append(charge_desc)
        charges_str = " | ".join(charges_list)
        
        f, m, l = _pn(full_name)
        
        def parse_irc_date_time(raw_str):
            if not raw_str:
                return "", ""
            clean_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw_str)
            try:
                dt = datetime.strptime(clean_str, "%B %d, %Y at %I:%M %p")
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            except Exception:
                date_match = re.search(r"([A-Za-z]+\s+\d+,\s+\d{4})", clean_str)
                time_match = re.search(r"(\d+:\d+\s+[ap]m)", clean_str, re.IGNORECASE)
                d_val = ""
                t_val = ""
                if date_match:
                    try:
                        d_val = datetime.strptime(date_match.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
                    except Exception:
                        pass
                if time_match:
                    t_val = time_match.group(1)
                return d_val, t_val

        b_date, b_time = parse_irc_date_time(booking_date_raw)
        a_date, a_time = parse_irc_date_time(arrest_date_raw)
        
        return {
            "County": "Indian River",
            "Booking_Number": booking_number,
            "Full_Name": full_name,
            "First_Name": f,
            "Middle_Name": m,
            "Last_Name": l,
            "DOB": dob,
            "Arrest_Date": a_date or arrest_date_raw,
            "Arrest_Time": a_time,
            "Booking_Date": b_date or booking_date_raw,
            "Booking_Time": b_time,
            "Status": "In Custody",
            "Facility": FACILITY,
            "Agency": agency,
            "Race": race,
            "Sex": sex,
            "Height": height,
            "Weight": weight,
            "Address": address,
            "Charges": charges_str,
            "Bond_Amount": str(bond_amount) if bond_amount > 0 else "0",
            "Bond_Type": bond_type,
            "Case_Number": case_number,
            "Detail_URL": detail_url
        }
    except Exception as e:
        print("Fetch failed:", e)
        return None

if __name__ == "__main__":
    record = _fetch_single_booking("1725627172", "https://www.ircsheriff.org/booking-details/1725627172")
    if record:
        print("\nSuccessfully Parsed Record:")
        for k, v in record.items():
            print(f"  {k}: {v}")
