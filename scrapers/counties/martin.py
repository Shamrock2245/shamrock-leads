"""
Martin County Arrest Scraper — Direct REST API.
Source: https://correctionsrecordssearch.com/martincountyfl
Method: Direct Tyler Technologies API queries

2026-10-15: plain ``requests`` via ``scrapers.plain_http`` (honest User-Agent,
TLS verified; no curl_cffi Chrome impersonation, no ``verify=False``). A block
raises EgressBlocked and a bad certificate raises SourceTLSInvalid.
Status comes only from the published ``IsReleased`` flag ("" when absent).
Bond is the sum of published bond amounts only when every bond publishes one,
else "" (never an invented "0"; a published 0 is kept). Rows without a source
``soNumber`` are skipped (the API's internal ``id`` is not a booking number).
State is always FL (the jail's state), never the defendant's address state.
"""
import logging
import datetime
from typing import List
from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Martin County Jail"
COUNTY = "Martin"
API_URL = "https://api.correctionsrecordssearch.com/instances/01K343RER5XCX3V9KQA5876BE8/inmates"

class MartinCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Martin"

    def scrape(self) -> List[ArrestRecord]:
        logger.info("Martin: Querying inmate API...")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://correctionsrecordssearch.com",
            "Referer": "https://correctionsrecordssearch.com/"
        }
        
        # We fetch 100 recent arrests
        params = {
            "page-size": 100,
            "page-number": 1,
            "sort-by": "arrestDate",
            "sort": "desc"
        }
        
        try:
            session = make_session(headers)
            resp = request(session, "GET", API_URL, params=params, timeout=30, county="Martin")
            if resp.status_code != 200:
                raise Exception(f"Failed to fetch roster: HTTP {resp.status_code}")
            
            data = resp.json()
        except Exception as e:
            logger.error(f"Martin roster fetch failed: {e}")
            raise

        inmates = data.get("inmates", []) if isinstance(data, dict) else data
        logger.info(f"Martin: Found {len(inmates)} inmates in current API page")

        records = []
        for x in inmates:
            try:
                name_info = x.get("name", {})
                first_name = name_info.get("firstName", "").strip()
                middle_name = name_info.get("middleName", "").strip()
                last_name = name_info.get("lastName", "").strip()
                full_name = name_info.get("fullName", "").strip()
                
                if not full_name:
                    full_name = f"{last_name}, {first_name}"
                    if middle_name:
                        full_name += f" {middle_name}"
                
                so_number = str(x.get("soNumber") or "").strip()
                if not so_number:
                    continue  # no source booking key; never fall back to the internal id
                    
                dob = x.get("dateOfBirth", "").strip()
                
                # Status
                is_released = x.get("IsReleased")
                if is_released is True:
                    status = "Released"
                elif is_released is False:
                    status = "In Custody"  # published flag, not an assumption
                else:
                    status = ""
                
                # Physicals
                height = x.get("height", "").strip()
                weight = x.get("weight", "").strip()
                race = x.get("race", "").strip()
                sex = x.get("gender", "").strip()
                
                # Mugshot (if any image ID is present)
                image_id = x.get("imageServiceClientId")
                mugshot_url = ""
                if image_id:
                    mugshot_url = f"https://api.correctionsrecordssearch.com/instances/01K343RER5XCX3V9KQA5876BE8/images/{image_id}"
                
                # Find arrest details (booking date/time, charges)
                arrests = x.get("arrests", [])
                booking_date = ""
                booking_time = ""
                charges_list = []
                agency = ""
                age_at_arrest = ""
                
                if arrests:
                    # Sort arrests by arrestDate desc just in case
                    arrests_sorted = sorted(
                        arrests, 
                        key=lambda a: a.get("arrestDate", ""), 
                        reverse=True
                    )
                    latest_arrest = arrests_sorted[0]
                    dt_str = latest_arrest.get("arrestDate", "") # e.g. "2026-05-24T07:47:00"
                    if "T" in dt_str:
                        booking_date, booking_time = dt_str.split("T", 1)
                    else:
                        booking_date = dt_str
                    
                    agency = latest_arrest.get("arrestingAgency", "")
                    age_at_arrest = str(latest_arrest.get("ageAtArrest", ""))
                    
                    # Accumulate charges from all arrests in this booking
                    for arr in arrests:
                        for chg in arr.get("charges", []):
                            stat = chg.get("statute", {})
                            desc = stat.get("description", "").strip()
                            code = stat.get("statuteCode", "").strip() # check if code exists
                            if not code:
                                code = stat.get("code", "").strip()
                            if desc:
                                if code:
                                    charges_list.append(f"{code} - {desc}")
                                else:
                                    charges_list.append(desc)
                
                charges_str = " | ".join(charges_list)
                
                # Bond: total only when every bond publishes an amount
                bond_amounts = []
                bond_types = []
                for b in x.get("bonds") or []:
                    bond_amounts.append(charge_bond_amount(b.get("amount")))
                    btype = str(b.get("type") or "").strip()
                    if btype and btype not in bond_types:
                        bond_types.append(btype)

                bond_amount_str = total_published_bond(bond_amounts)
                bond_type_str = ", ".join(bond_types)
                
                # City, State
                addr_info = x.get("address") or {}
                city = str(addr_info.get("city") or "").strip()
                source_booking_date = str(x.get("bookingDate") or "").strip()
                if source_booking_date:
                    if "T" in source_booking_date:
                        bd, bt = source_booking_date.split("T", 1)
                    else:
                        bd, bt = source_booking_date, ""
                else:
                    bd, bt = booking_date, booking_time
                
                records.append(ArrestRecord(
                    Scrape_Timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    County=self.county,
                    Booking_Number=so_number,
                    Full_Name=full_name.upper(),
                    First_Name=first_name.upper(),
                    Middle_Name=middle_name.upper(),
                    Last_Name=last_name.upper(),
                    DOB=dob,
                    Arrest_Date=booking_date,
                    Arrest_Time=booking_time,
                    Booking_Date=bd,
                    Booking_Time=bt,
                    Status=status,
                    Facility=FACILITY,
                    Agency=agency,
                    Race=race,
                    Sex=sex,
                    Height=height,
                    Weight=weight,
                    Age_At_Arrest=age_at_arrest,
                    City=city.upper(),
                    State="FL",
                    Mugshot_URL=mugshot_url,
                    Charges=charges_str,
                    Bond_Amount=bond_amount_str,
                    Bond_Type=bond_type_str,
                    Detail_URL=f"https://correctionsrecordssearch.com/martincountyfl/inmates/{x.get('id')}",
                    LastCheckedMode="INITIAL"
                ))
            except Exception as ex:
                logger.warning(f"Martin: failed to parse inmate record: {ex}", exc_info=True)
                
        logger.info(f"Martin: parsed {len(records)} records")
        return records
