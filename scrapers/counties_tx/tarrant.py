"""
Tarrant County (TX) Arrest Scraper — Official Jail & Magistration Docket System.

Portal: https://inmatesearch.tarrantcounty.com/
Tarrant County is the 3rd-largest TX county (~2.1M pop) encompassing Fort Worth.
Uses stealth stack (make_stealth_request, curl_cffi, APE proxy, BehaviorSimulator)
to query magistration docket and active inmate search endpoints.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Dict, List, Set, Tuple

from scrapers.base_scraper import BaseScraper
from scrapers.stealth_utils import make_stealth_request, BehaviorSimulator
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://inmatesearch.tarrantcounty.com/"
DOCKET_URL = "https://inmatesearch.tarrantcounty.com/Home/GetDocketResults"
SEARCH_URL = "https://inmatesearch.tarrantcounty.com/Home/GetSearchResults"


class TarrantScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Tarrant"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen_cid: Set[str] = set()

        # Step 1: Scrape live magistration docket (gives high-value offenses + agencies)
        docket_records, docket_cid_map = self._scrape_magistration_docket(seen_cid)
        records.extend(docket_records)

        # Step 2: Walk top last-name letter prefixes on inmate search endpoint
        search_records = self._scrape_inmate_search(seen_cid, docket_cid_map)
        records.extend(search_records)

        logger.info(
            f"✅ Tarrant (TX): {len(records)} records "
            f"({len(docket_records)} docket, {len(search_records)} search) "
            f"in {time.time() - start:.1f}s"
        )
        return records

    def _scrape_magistration_docket(
        self, seen_cid: Set[str]
    ) -> Tuple[List[ArrestRecord], Dict[str, Dict]]:
        out: List[ArrestRecord] = []
        cid_map: Dict[str, Dict] = {}

        try:
            # Query docket in pages of 50
            for start in (0, 50, 100, 150):
                payload = {
                    "jtStartIndex": str(start),
                    "jtPageSize": "50",
                    "jtSorting": "LastName ASC",
                }
                try:
                    resp = make_stealth_request(
                        DOCKET_URL, method="POST", data=payload, timeout=15
                    )
                    if not resp or resp.status_code != 200:
                        break

                    data = resp.json()
                    if data.get("Result") != "OK":
                        break

                    batch = data.get("Records", [])
                    if not batch:
                        break

                    for r in batch:
                        cid = str(r.get("CID") or "").strip()
                        if not cid:
                            continue

                        last = (r.get("LastName") or "").strip().title()
                        first_mid = (r.get("FirstMiddleName") or "").strip().title()
                        offense = (r.get("Offense") or "").strip()
                        agency = (r.get("Agency") or "").strip().title()
                        docket_time = (r.get("DocketTime") or "").strip()

                        cid_map[cid] = {
                            "offense": offense,
                            "agency": agency,
                            "docket_time": docket_time,
                        }

                        if cid in seen_cid:
                            continue
                        seen_cid.add(cid)

                        full_name = f"{last}, {first_mid}".strip(", ")
                        first_name, last_name = self._split_name(full_name)

                        rec = ArrestRecord(
                            County=self.county,
                            State=self.state,
                            Booking_Number=f"TAR_{cid}",
                            Person_ID=cid,
                            Full_Name=full_name,
                            First_Name=first_name,
                            Last_Name=last_name,
                            Charges=offense or "Magistration Docket",
                            Bond_Amount="0",  # Will be populated/scored
                            Status="In Custody",
                            Facility="Tarrant County Jail",
                            Agency=agency or "Tarrant County Sheriff's Office",
                            Detail_URL=f"{PORTAL_URL}Home/Details?CID={cid}",
                        )
                        out.append(rec)

                    if len(batch) < 50:
                        break

                except Exception as e:
                    logger.debug(f"Tarrant docket page start={start} failed: {e}")
                    break

        except Exception as e:
            logger.error(f"Tarrant docket scrape error: {e}")

        return out, cid_map

    def _scrape_inmate_search(
        self, seen_cid: Set[str], docket_cid_map: Dict[str, Dict]
    ) -> List[ArrestRecord]:
        out: List[ArrestRecord] = []
        letters = ("A", "B", "C", "D", "E", "F", "G", "H", "J", "M", "R", "S", "T", "W")

        for prefix in letters:
            try:
                payload = {
                    "lastName": prefix,
                    "firstName": "",
                    "cid": "",
                    "raceId": "All",
                    "sexId": "Both",
                    "recordsId": "50",
                    "jtStartIndex": "0",
                    "jtPageSize": "50",
                    "jtSorting": "FirstMiddleName ASC",
                }
                resp = make_stealth_request(
                    SEARCH_URL, method="POST", data=payload, timeout=15
                )
                if not resp or resp.status_code != 200:
                    continue

                data = resp.json()
                if data.get("Result") != "OK":
                    continue

                batch = data.get("Records", [])
                for r in batch:
                    cid = str(r.get("CID") or "").strip()
                    if not cid or cid in seen_cid:
                        continue
                    seen_cid.add(cid)

                    last = (r.get("LastName") or "").strip().title()
                    first_mid = (r.get("FirstMiddleName") or "").strip().title()
                    race = (r.get("Race") or "").strip()
                    sex = (r.get("Sex") or "").strip()
                    dob = (r.get("DOB") or "").strip()

                    full_name = f"{last}, {first_mid}".strip(", ")
                    first_name, last_name = self._split_name(full_name)

                    # Enrich with magistration docket offense if matched
                    dock_info = docket_cid_map.get(cid, {})
                    offense = dock_info.get("offense", "")
                    agency = dock_info.get("agency", "")

                    rec = ArrestRecord(
                        County=self.county,
                        State=self.state,
                        Booking_Number=f"TAR_{cid}",
                        Person_ID=cid,
                        Full_Name=full_name,
                        First_Name=first_name,
                        Last_Name=last_name,
                        DOB=dob or None,
                        Race=race or None,
                        Sex=sex[:1].upper() if sex else None,
                        Charges=offense or "Inmate Booking",
                        Bond_Amount="0",
                        Status="In Custody",
                        Facility="Tarrant County Jail",
                        Agency=agency or "Tarrant County Sheriff's Office",
                        Detail_URL=f"{PORTAL_URL}Home/Details?CID={cid}",
                    )
                    out.append(rec)

            except Exception as e:
                logger.debug(f"Tarrant inmate search prefix={prefix} failed: {e}")

        return out

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        name = name.strip()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
