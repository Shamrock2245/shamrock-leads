"""
Volusia County Arrest Scraper — Direct ASP.NET Postback Search.
Source: Volusia County Division of Corrections Inmate Inquiry
URL: https://volusiamug.vcgov.org/
Method: Browserless ASP.NET session postback + details parse
"""
import logging
import datetime
import re
from typing import List
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.plain_http import make_session, request
from scrapers.scraper_resilience import AntiBotBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Volusia County Branch Jail"
COUNTY = "Volusia"
BASE_URL = "https://volusiamug.vcgov.org/"

class VolusiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Volusia"

    def scrape(self) -> List[ArrestRecord]:
        logger.info("Volusia: Querying inmate search...")
        
        session = make_session()
        
        try:
            # Step 1: GET disclaimer page
            resp = request(session, "GET", BASE_URL, timeout=20, county="Volusia")
            if resp.status_code != 200:
                raise Exception(f"Failed to fetch Volusia homepage: HTTP {resp.status_code}")
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            viewstate_el = soup.find('input', {'name': '__VIEWSTATE'})
            generator_el = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
            validation_el = soup.find('input', {'name': '__EVENTVALIDATION'})
            
            if not viewstate_el:
                raise Exception("Missing ViewState on homepage")
                
            viewstate = viewstate_el.get('value')
            generator = generator_el.get('value') if generator_el else ""
            validation = validation_el.get('value') if validation_el else ""
            
            # Step 2: POST disclaimer accept
            payload = {
                "__VIEWSTATE": viewstate,
                "__VIEWSTATEGENERATOR": generator,
                "__EVENTVALIDATION": validation,
                "ButtonAccept": "Accept"
            }
            
            post_url = "https://volusiamug.vcgov.org/Disclaimer.aspx"
            post_resp = request(session, "POST", post_url, data=payload, timeout=20, county="Volusia")
            if post_resp.status_code != 200:
                raise Exception(f"Failed to submit disclaimer accept: HTTP {post_resp.status_code}")
                
            # Step 3: POST to Default.aspx to request Recent Bookings
            search_soup = BeautifulSoup(post_resp.text, 'html.parser')
            s_viewstate_el = search_soup.find('input', {'name': '__VIEWSTATE'})
            if not s_viewstate_el:
                raise Exception("Missing ViewState on search page")
                
            s_viewstate = s_viewstate_el.get('value')
            s_generator = search_soup.find('input', {'name': '__VIEWSTATEGENERATOR'}).get('value') if search_soup.find('input', {'name': '__VIEWSTATEGENERATOR'}) else ""
            s_validation = search_soup.find('input', {'name': '__EVENTVALIDATION'}).get('value') if search_soup.find('input', {'name': '__EVENTVALIDATION'}) else ""
            
            recent_payload = {
                "__VIEWSTATE": s_viewstate,
                "__VIEWSTATEGENERATOR": s_generator,
                "__EVENTVALIDATION": s_validation,
                "txtBookingNo": "",
                "txtLname": "",
                "txtFName": "",
                "btnRecentBookings": "Recent"
            }
            
            recent_url = "https://volusiamug.vcgov.org/Default.aspx"
            recent_resp = request(session, "POST", recent_url, data=recent_payload, timeout=20, county="Volusia")
            if recent_resp.status_code != 200:
                raise Exception(f"Failed to fetch recent bookings list: HTTP {recent_resp.status_code}")
                
            recent_soup = BeautifulSoup(recent_resp.text, 'html.parser')
            table = recent_soup.find('table')
            if not table:
                logger.warning("Volusia: No bookings table found on recent list page")
                return []
                
            rows = table.find_all('tr')
            logger.info(f"Volusia: Found {len(rows)-1} total bookings listed in table")
            
            records = []
            
            # Iterate through bookings rows
            for r in rows[1:]:
                cells = r.find_all('td')
                if len(cells) < 10:
                    continue
                    
                booking_no = cells[0].get_text(strip=True)
                inmate_id = cells[2].get_text(strip=True)
                last_name = cells[3].get_text(strip=True)
                first_name = cells[4].get_text(strip=True)
                middle_name = cells[5].get_text(strip=True)
                suffix = cells[6].get_text(strip=True)
                sex = cells[7].get_text(strip=True)
                race = cells[8].get_text(strip=True)
                booking_date_raw = cells[9].get_text(strip=True)
                release_date_raw = cells[10].get_text(strip=True)
                in_custody = cells[11].get_text(strip=True)
                
                # Check for details link
                link = r.find('a', href=True)
                if not link:
                    continue
                    
                href = link['href']
                inmate_rid_m = re.search(r"InmateRID=(\d+)", href)
                if not inmate_rid_m:
                    continue
                    
                inmate_rid = inmate_rid_m.group(1)
                detail_url = f"https://volusiamug.vcgov.org/Details.aspx?InmateRID={inmate_rid}"
                
                # Setup names
                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"
                if suffix:
                    full_name += f" {suffix}"
                
                # Release status
                status = "Released" if release_date_raw or in_custody == 'N' else "In Custody"
                
                # We have booking date/time, e.g. "05/24/2026 14:32"
                booking_date = ""
                booking_time = ""
                if " " in booking_date_raw:
                    booking_date, booking_time = booking_date_raw.split(" ", 1)
                else:
                    booking_date = booking_date_raw
                    
                release_date = ""
                if " " in release_date_raw:
                    release_date, _ = release_date_raw.split(" ", 1)
                else:
                    release_date = release_date_raw
                
                # Demographics from list (we can supplement or override with details page)
                age = ""
                zip_code = ""
                city = ""
                state = "FL"
                charges_list = []
                charge_bonds = []  # published amounts per charge; None = unknown
                bond_types = []
                case_number = ""
                mugshot_url = f"https://volusiamug.vcgov.org/FullsizeMugshotHandler.ashx?InmateRID={inmate_rid}"
                
                # Fetch details page
                try:
                    logger.debug(f"Volusia: Fetching details for {full_name} ({inmate_rid})")
                    d_resp = request(session, "GET", detail_url, timeout=15, county="Volusia")
                    if d_resp.status_code == 200:
                        d_soup = BeautifulSoup(d_resp.text, 'html.parser')
                        
                        # Extract ZipCode, City, State, Age
                        zip_el = d_soup.find('input', {'id': 'txtZipCode'})
                        city_el = d_soup.find('input', {'id': 'txtCity'})
                        state_el = d_soup.find('input', {'id': 'txtState'})
                        age_el = d_soup.find('input', {'id': 'txtDOB'}) # Labeled Age but ID=txtDOB
                        
                        if zip_el:
                            zip_code = zip_el.get('value', '').strip()
                        if city_el:
                            city = city_el.get('value', '').strip()
                        if state_el:
                            state = state_el.get('value', 'FL').strip()
                        if age_el:
                            age = age_el.get('value', '').strip()
                            
                        # Parse Charges grid table
                        d_tables = d_soup.find_all('table')
                        for dt in d_tables:
                            d_rows = dt.find_all('tr')
                            if not d_rows:
                                continue
                            header_cells = d_rows[0].find_all(['td', 'th'])
                            header_texts = [hc.get_text(strip=True).lower() for hc in header_cells]
                            
                            # Identify charges table by headers
                            if 'charge #' in header_texts and 'statute description' in header_texts:
                                # This is the charges table
                                for dr in d_rows[1:]:
                                    d_cells = dr.find_all('td')
                                    if len(d_cells) < 5:
                                        continue
                                    # Columns: Charge#, Statute, Statute Description, Bond Type, Bond Amount
                                    c_statute = d_cells[1].get_text(strip=True)
                                    c_desc = d_cells[2].get_text(strip=True)
                                    c_bond_type = d_cells[3].get_text(strip=True)
                                    c_bond_amount = d_cells[4].get_text(strip=True)
                                    
                                    # Collect case number if present (Court Case # is column 7)
                                    if len(d_cells) >= 8:
                                        c_case = d_cells[7].get_text(strip=True)
                                        if c_case and not case_number:
                                            case_number = c_case
                                            
                                    charge_str = f"{c_statute} - {c_desc}" if c_statute and c_desc else c_statute or c_desc
                                    if charge_str:
                                        charges_list.append(charge_str)
                                        
                                    if c_bond_type and c_bond_type not in bond_types:
                                        bond_types.append(c_bond_type)
                                        
                                    # Parse bond amount
                                    charge_bonds.append(charge_bond_amount(c_bond_amount))
                except AntiBotBlocked:
                    raise
                except Exception as dex:
                    logger.warning(f"Volusia: Failed to parse details for inmate_rid {inmate_rid}: {dex}")
                
                charges_str = " | ".join(charges_list)
                bond_amount_str = total_published_bond(charge_bonds)
                bond_type_str = ", ".join(bond_types)
                
                records.append(ArrestRecord(
                    Scrape_Timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    County=self.county,
                    Booking_Number=booking_no,
                    Person_ID=inmate_id,
                    Full_Name=full_name.upper(),
                    First_Name=first_name.upper(),
                    Middle_Name=middle_name.upper(),
                    Last_Name=last_name.upper(),
                    DOB="", # Birthdate not published, we could compute from age if needed
                    Arrest_Date=booking_date,
                    Arrest_Time=booking_time,
                    Booking_Date=booking_date,
                    Booking_Time=booking_time,
                    Status=status,
                    Release_Date=release_date,
                    Facility=FACILITY,
                    Race=race,
                    Sex=sex,
                    Age_At_Arrest=age,
                    City=city.upper(),
                    State=state.upper(),
                    ZIP=zip_code,
                    Mugshot_URL=mugshot_url,
                    Charges=charges_str,
                    Bond_Amount=bond_amount_str,
                    Bond_Type=bond_type_str,
                    Case_Number=case_number,
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL"
                ))
                
            logger.info(f"Volusia: parsed {len(records)} records")
            return records
        except AntiBotBlocked:
            raise
        except Exception as e:
            logger.error(f"Volusia roster fetch failed: {e}")
            raise
            
    @staticmethod
    def _parse_bond_val(bond_str: str):
        """Published amount (a real 0 included) or None when not published."""
        return charge_bond_amount(bond_str)
