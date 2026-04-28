"""Orange County Arrest Scraper — BestJail JSON API (pure HTTP)."""
import logging
import re
import string
import time
import requests
from datetime import datetime, timezone
from typing import List, Optional
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://netapps.ocfl.net/BestJail/Home"
INMATES_URL = f"{BASE_URL}/getInmates"
DETAILS_URL = f"{BASE_URL}/getInmateDetails"
CHARGES_URL = f"{BASE_URL}/getCharges"
DETAIL_DELAY_S = 0.15
LETTER_DELAY_S = 0.1
REQUEST_TIMEOUT = 15
CURRENT_YEAR_PREFIX = str(datetime.now().year)[-2:]
DAYS_BACK = 90  # Extended: capture all in-custody inmates
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Inmates",
}

class OrangeCountyScraper(BaseScraper):
    @property
    def county(self): return "Orange"
    @property
    def roster_url(self): return f"{BASE_URL}/Inmates"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        all_inmates = self._fetch_all_inmates(session)
        if not all_inmates:
            logger.warning("Orange: No inmates found")
            return []
        recent = [i for i in all_inmates if i.get("bookingNumber","").startswith(CURRENT_YEAR_PREFIX)]
        logger.info(f"Orange: {len(all_inmates)} total, {len(recent)} from 20{CURRENT_YEAR_PREFIX}")
        records, enriched, skipped = [], 0, 0
        for inmate in recent:
            bn = inmate.get("bookingNumber","").strip()
            if not bn: continue
            detail = self._fetch_detail(session, bn)
            if not detail: continue
            bd = self._parse_date(detail)
            if bd and (datetime.now(timezone.utc) - bd).days > DAYS_BACK:
                skipped += 1; continue
            charges = self._fetch_charges(session, bn)
            rec = self._build(detail, charges, bn)
            if rec: records.append(rec); enriched += 1
            if enriched % 25 == 0 and enriched > 0:
                logger.info(f"  Orange: {enriched} enriched...")
            time.sleep(DETAIL_DELAY_S)
        logger.info(f"Orange: {enriched} recent (skipped {skipped} old)")
        return records

    def _fetch_all_inmates(self, s):
        all_i, seen = [], set()
        for letter in string.ascii_lowercase:
            try:
                r = s.get(f"{INMATES_URL}/{letter}", timeout=REQUEST_TIMEOUT)
                if r.status_code != 200: continue
                for i in r.json():
                    bn = i.get("bookingNumber","").strip()
                    if bn and bn not in seen: seen.add(bn); all_i.append(i)
                time.sleep(LETTER_DELAY_S)
            except: continue
        return all_i

    def _fetch_detail(self, s, bn):
        try:
            r = s.get(f"{DETAILS_URL}/{bn}", timeout=REQUEST_TIMEOUT)
            d = r.json()
            return d[0] if isinstance(d, list) and d else None
        except: return None

    def _fetch_charges(self, s, bn):
        try:
            r = s.get(f"{CHARGES_URL}/{bn}", timeout=REQUEST_TIMEOUT)
            d = r.json()
            return d if isinstance(d, list) else []
        except: return []

    def _parse_date(self, d):
        ds, ts = d.get("DATEBOOKED","").strip(), d.get("TIMEBOOKED","").strip()
        if not ds: return None
        try:
            fmt = f"{ds} {ts}" if ts else ds
            pat = "%m/%d/%Y %I:%M%p" if ts else "%m/%d/%Y"
            return datetime.strptime(fmt, pat).replace(tzinfo=timezone.utc)
        except: return None

    def _build(self, detail, charges, bn):
        try:
            name = detail.get("NAME","").strip()
            if not name: return None
            first, last = "", ""
            if "," in name:
                p = name.split(",",1); last = p[0].strip().title()
                fp = p[1].strip().split(); first = fp[0].title() if fp else ""
            else: first = name.title()
            cl, tb, aa = [], 0.0, ""
            for c in charges:
                ct = c.get("Charge","").strip()
                if ct:
                    if "-" in ct: ct = ct.split("-",1)[-1].strip()
                    cl.append(ct)
                try: tb += float(re.sub(r"[,$]","",c.get("BondAmount","0").strip()))
                except: pass
                if not aa: aa = c.get("ArrestingAgency","").strip()
            st = detail.get("STREET","").strip()
            apt = detail.get("APTNUM","").strip()
            city = detail.get("CITY","").strip()
            state = detail.get("STATE","").strip()
            zc = detail.get("ZIPCODE","").strip()
            parts = [st]
            if apt: parts.append(f"Apt {apt}")
            if city: parts.append(city)
            if state: parts.append(state)
            if zc: parts.append(zc)
            addr = ", ".join(p for p in parts if p)
            bd = self._parse_date(detail)
            bds = bd.strftime("%m/%d/%Y") if bd else ""
            bts = bd.strftime("%I:%M %p") if bd else ""
            release_date = ""
            for rk in ["RELEASED_DATE", "RELEASE_DATE", "ReleaseDate", "releaseDate"]:
                if rk in detail and detail[rk]:
                    release_date = str(detail[rk]).strip(); break
            return ArrestRecord(
                County="Orange", Booking_Number=bn, First_Name=first, Last_Name=last,
                Full_Name=f"{first} {last}".strip(), Booking_Date=bds, Booking_Time=bts,
                Charges="; ".join(cl) if cl else "Not Available",
                Bond_Amount=f"${tb:,.2f}" if tb > 0 else "$0.00",
                Race=detail.get("RACE","").strip().title(),
                Sex=detail.get("GENDER","").strip().title(),
                DOB=detail.get("BIRTH","").strip(), Address=addr,
                City=city.title() if city else "",
                State=state, ZIP=zc,
                Agency=aa.title() if aa else "Orange County Sheriff Office",
                Facility="Orange County Jail",
                Status="Released" if release_date else "In Custody",
                Release_Date=release_date,
                Detail_URL=BASE_URL
            )
        except Exception as e:
            logger.warning(f"Orange build error {bn}: {e}"); return None
