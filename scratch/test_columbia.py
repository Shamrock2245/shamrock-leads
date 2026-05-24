import requests
from bs4 import BeautifulSoup
import json
import re

BASE_URL = "http://50.204.15.10"
SEARCH_URL = f"{BASE_URL}/smartwebclient/Jail.aspx"
ADD_MORE_URL = f"{BASE_URL}/smartwebclient/Jail.aspx/AddMoreResults"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}

def parse_name(name_str: str):
    if not name_str:
        return "", "", ""
    name_str = " ".join(name_str.strip().split())
    if "," in name_str:
        parts = name_str.split(",", 1)
        last = parts[0].strip()
        fm = parts[1].strip().split()
        first = fm[0] if fm else ""
        middle = " ".join(fm[1:]) if len(fm) > 1 else ""
        return first, middle, last
    parts = name_str.split()
    return parts[0], "", parts[-1] if len(parts) >= 2 else ""

def parse_bond_val(bond_str: str) -> float:
    if not bond_str:
        return 0.0
    cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
    if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
        return 0.0
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0

def parse_full_inmate_details(soup):
    records = []
    headers = soup.find_all("td", class_="SearchHeader")
    for header_td in headers:
        try:
            header_text = header_td.get_text(" ", strip=True)
            # Parse format: "LAST, FIRST MIDDLE (R/SEX / DOB: MM/DD/YYYY )"
            header_match = re.search(
                r"([A-Z\s,'\-\.]+)\s*\(([A-Z])/\s*(MALE|FEMALE|M|F)\s*/\s*DOB:\s*([\d/]+)\s*\)",
                header_text,
                re.IGNORECASE
            )
            if not header_match:
                continue
            
            full_name = header_match.group(1).strip()
            race = header_match.group(2).strip()
            sex_raw = header_match.group(3).strip()
            sex = "M" if sex_raw.upper() in ("MALE", "M") else "F"
            dob = header_match.group(4).strip()
            
            first_name, middle_name, last_name = parse_name(full_name)

            # Extract card sub-table
            detail_table = header_td.find_parent("table")
            if not detail_table:
                continue
            
            detail_text = detail_table.get_text(" ", strip=True)
            
            booking_no_match = re.search(r"Booking\s+No[:\s]+([A-Z0-9]+)", detail_text, re.IGNORECASE)
            booking_number = booking_no_match.group(1).strip() if booking_no_match else ""
            if not booking_number:
                continue
            
            booking_date_match = re.search(r"Booking\s+Date[:\s]+([\d/]+\s+[\d:]+\s*(?:AM|PM)?)", detail_text, re.IGNORECASE)
            booking_date = booking_date_match.group(1).strip() if booking_date_match else ""

            address_match = re.search(r"Address\s+Given[:\s]+(.+?)(?:HOLDS|CHARGES|$)", detail_text, re.IGNORECASE | re.DOTALL)
            address = " ".join(address_match.group(1).strip().split()) if address_match else ""

            # Charges extraction: stop at next inmate header TD containing DOB:
            charges_list = []
            total_bond = 0.0
            
            charges_table = None
            top_row = detail_table.find_parent("tr")
            if top_row:
                sibling = top_row.find_next_sibling("tr")
                while sibling:
                    next_header = sibling.find("td", class_="SearchHeader")
                    if next_header and "DOB:" in next_header.get_text():
                        break
                    
                    table_el = sibling.find("table", class_="JailViewCharges")
                    if table_el:
                        charges_table = table_el
                        break
                    sibling = sibling.find_next_sibling("tr")
            
            if charges_table:
                chg_rows = charges_table.find_all("tr")
                for chg_row in chg_rows:
                    if chg_row.get("class") and "SearchHeader" in chg_row.get("class"):
                        continue
                    
                    cells = chg_row.find_all("td")
                    if len(cells) >= 6:
                        statute = cells[1].get_text(strip=True)
                        desc = cells[3].get_text(strip=True)
                        bond_str = cells[6].get_text(strip=True) if len(cells) >= 7 else ""
                        
                        if statute or desc:
                            item = f"{statute} - {desc}" if statute and desc else statute or desc
                            charges_list.append(item)
                            
                        bond_val = parse_bond_val(bond_str)
                        total_bond += bond_val
            
            charges_str = " | ".join(charges_list)
            records.append({
                "Booking_Number": booking_number,
                "Full_Name": full_name,
                "DOB": dob,
                "Booking_Date": booking_date,
                "Charges": charges_str,
                "Bond_Amount": str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                "Address": address,
                "Race": race,
                "Sex": sex
            })
        except Exception as e:
            print(f"Error parsing row: {e}")
            continue
    return records

def test_scrape():
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. GET standard ASP.NET state tokens
    resp = session.get(SEARCH_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def _get_hidden(name):
        el = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
        return el["value"] if el and el.get("value") else ""

    viewstate = _get_hidden("__VIEWSTATE")
    viewstate_generator = _get_hidden("__VIEWSTATEGENERATOR")
    event_validation = _get_hidden("__EVENTVALIDATION")

    # 2. Perform search with % wildcard
    post_data = {
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstate_generator,
        "__EVENTVALIDATION": event_validation,
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "txbLastName": "%",
        "txbFirstName": "",
        "tbDateOfBirth": "",
        "TypeSearch": "0",  # Current Inmates Only
        "SearchSortOption": "0", # Sorted by Name
        "SearchOrderOption": "0", # Ascending
        "btnSumit": "Submit",
    }

    resp2 = session.post(SEARCH_URL, data=post_data, timeout=30)
    resp2.raise_for_status()

    soup2 = BeautifulSoup(resp2.text, "html.parser")
    initial_records = parse_full_inmate_details(soup2)
    print(f"Initial search parsed {len(initial_records)} full inmate records.")
    for idx, r in enumerate(initial_records[:3]):
        print(f"  Inmate {idx+1}:")
        print(f"    Name: {r['Full_Name']} ({r['Sex']}/{r['Race']} DOB: {r['DOB']})")
        print(f"    Booking #: {r['Booking_Number']} on {r['Booking_Date']}")
        print(f"    Address: {r['Address']}")
        print(f"    Charges: {r['Charges']}")
        print(f"    Bond: ${r['Bond_Amount']}")

    # 3. Call AddMoreResults to load the next 20 records
    payload = {
        "searchVals": {
            "FirstName": "",
            "MiddleName": "",
            "LastName": "%",
            "BeginBookDate": "",
            "EndBookDate": "",
            "BeginReleaseDate": "",
            "EndReleaseDate": "",
            "TypeJailSearch": 0,
            "RecordsLoaded": len(initial_records),
            "SortOption": 0,
            "SortOrder": 0,
            "IsDefault": False,
            "DateOfBirth": "",
            "BookingNumber": ""
        }
    }

    json_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": SEARCH_URL
    }

    resp3 = session.post(ADD_MORE_URL, json=payload, headers=json_headers, timeout=30)
    resp3.raise_for_status()
    
    html_data = resp3.json().get("d", {}).get("data", "")
    soup3 = BeautifulSoup(html_data, "html.parser")
    more_records = parse_full_inmate_details(soup3)
    
    print(f"\nAddMoreResults parsed {len(more_records)} new full inmate records.")
    for idx, r in enumerate(more_records[:3]):
        print(f"  Inmate {idx+1+len(initial_records)}:")
        print(f"    Name: {r['Full_Name']} ({r['Sex']}/{r['Race']} DOB: {r['DOB']})")
        print(f"    Booking #: {r['Booking_Number']} on {r['Booking_Date']}")
        print(f"    Address: {r['Address']}")
        print(f"    Charges: {r['Charges']}")
        print(f"    Bond: ${r['Bond_Amount']}")

if __name__ == "__main__":
    test_scrape()
