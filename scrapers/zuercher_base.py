"""
Base scraper for Zuercher Technologies Portals.
Used by GA and SC counties (Anderson, Cherokee, Colleton, Kershaw, Laurens,
Oconee, Pickens, Union in SC; several mid-tier GA counties).

Verified 2026-07-20: modern Zuercher portals expose a JSON API at
``POST /api/portal/inmates/load`` — no headless browser needed.
Response shape:
    {"total_record_count": N, "records": [{"name": "LAST, FIRST M",
      "sex": ..., "arrest_date": "YYYY-MM-DD", "hold_reasons": "...",
      "mugshot": "<base64 jpeg>", "is_juvenile": false, ...}]}

Charges + bond amounts are embedded in the free-text ``hold_reasons`` field:
    "Warrant Charge: <desc> warrant <num> issued by ...; Arrest Date MM/DD/YYYY; Bond - $1,000.00;"
"""

import hashlib
import logging
import re
import time
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# Zuercher portal JSON API payload — include_all pulls the full roster
PORTAL_API_PATH = "/api/portal/inmates/load"
PORTAL_PAGE_SIZE = 1000

# hold_reasons parsers.
# Zuercher hold_reasons is a <br />-separated list of hold segments, each like:
#   "Warrant: Felony Arrest warrant 2025A15... issued by Colleton, SC; Arrest Date 10/11/2025; Bond - Cash/Surety, $50000.00; Set By ..."
#   "Warrant Charge: Family Court Bench Warrant warrant ... (63-05-0020 (A) - ...); Arrest Date 06/21/2026; Bond - $0.00;"
#   "FTP Support: Failure to Pay warrant 2004DR... issued by Colleton, SC; ..."
_HOLD_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_CHARGE_LABEL_RE = re.compile(
    r"^\s*([\w /()'&.-]+?):\s*(.+?)(?:\s+warrant\s+\S*)?(?:\s+issued by.*)?$",
    re.IGNORECASE,
)
_BOND_RE = re.compile(r"Bond\s*[-–]\s*(?:[\w/ ]+,\s*)?\$([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_ARREST_DATE_RE = re.compile(r"Arrest Date\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)


class ZuercherBaseScraper(BaseScraper):
    """
    Base scraper for Zuercher Technologies Portals.
    Subclasses only need to provide the county name and subdomain.
    """

    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")

    @property
    def zuercher_domain(self) -> str:
        """Hostname only, e.g. 'douglas-so-ga.zuercherportal.com'.

        Subclasses may define ``portal_url`` instead; domain is derived.
        """
        portal = getattr(self, "portal_url", None)
        if portal:
            from urllib.parse import urlparse
            url = portal() if callable(portal) else portal
            if not isinstance(url, str):
                url = str(url)
            host = urlparse(url).netloc or url.replace("https://", "").replace("http://", "").split("/")[0]
            return host
        raise NotImplementedError(
            "Subclasses must define zuercher_domain or portal_url "
            "(e.g., 'douglas-so-ga.zuercherportal.com')"
        )

    # ── Internal helpers ────────────────────────────────────────────────────

    def _stable_booking_number(self, name: str, arrest_date: str) -> str:
        """Deterministic fallback key (idempotent across runs — never time-based)."""
        st = getattr(self, "state", None) or "FL"
        digest = hashlib.md5(f"{name}|{arrest_date}|{self.county}|{st}".encode()).hexdigest()[:10]
        return f"ZP_{digest}"

    @staticmethod
    def _parse_hold_reasons(hold_reasons: str):
        """Extract (charges_list, total_bond_cents) from Zuercher hold_reasons text.

        Bond math is done in integer cents to avoid float drift, then
        rendered back to a dollar string.
        """
        charges: List[str] = []
        total_cents = 0
        if not hold_reasons:
            return charges, total_cents
        # Bond first (regex is segment-independent)
        for m in _BOND_RE.finditer(hold_reasons):
            try:
                dollars = m.group(1).replace(",", "")
                total_cents += int(round(float(dollars) * 100))
            except (ValueError, TypeError):
                pass
        # Charges: split on <br /> then on ';' — first clause of each hold
        # segment is the charge description
        for segment in _HOLD_SPLIT_RE.split(hold_reasons):
            first_clause = segment.split(";", 1)[0].strip()
            if not first_clause:
                continue
            m = _CHARGE_LABEL_RE.match(first_clause)
            if m:
                label = m.group(1).strip()
                desc = m.group(2).strip().rstrip(";").strip()
                text = desc if desc else label
                # Prefer the statute/description parenthetical when present
                if label.lower() in ("warrant", "charge", "warrant charge") and desc:
                    text = desc
                elif desc:
                    text = f"{label}: {desc}"
            else:
                text = first_clause
            # Trim trailing warrant numbers noise
            text = re.sub(r"\s+warrant\s+\S*\s*$", "", text, flags=re.IGNORECASE).strip()
            if text and text not in charges:
                charges.append(text[:200])
        return charges, total_cents

    @staticmethod
    def _split_listed_name(raw_name: str):
        """'LAST, FIRST MIDDLE' → (first, middle, last)."""
        if "," in raw_name:
            last, rest = raw_name.split(",", 1)
            rest_bits = rest.strip().split()
            first = rest_bits[0] if rest_bits else ""
            middle = " ".join(rest_bits[1:]) if len(rest_bits) > 1 else ""
            return first.title(), middle.title(), last.strip().title()
        bits = raw_name.split()
        if len(bits) >= 2:
            return bits[0].title(), " ".join(bits[1:-1]).title(), bits[-1].title()
        return raw_name.title(), "", ""

    # ── Scrape ──────────────────────────────────────────────────────────────

    def scrape(self) -> List[ArrestRecord]:
        """
        Fetch from Zuercher portal via the JSON API (no headless browser).
        """
        start_time = time.time()
        base_url = f"https://{self.zuercher_domain}"

        logger.info(f"📥 Fetching Zuercher roster for {self.county} at {base_url}")

        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": f"{base_url}/",
            })

            # Step 1: establish session cookie (ZPORTAL_SID)
            try:
                session.get(base_url, timeout=15)
            except Exception as exc:
                logger.debug(f"{self.county}: session warm-up failed (continuing): {exc}")

            # Step 2: pull the roster from the portal JSON API, paginated
            inmates = []
            start = 0
            total = None
            while True:
                payload = {
                    "name": "",
                    "race": "all",
                    "sex": "all",
                    "cell_block": "all",
                    "held_for_agency": "any",
                    "in_custody_date": "",
                    "include_all": True,
                    "paging": {"count": PORTAL_PAGE_SIZE, "start": start},
                    "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
                }
                try:
                    api_resp = session.post(
                        base_url + PORTAL_API_PATH, json=payload, timeout=20
                    )
                except Exception as exc:
                    logger.error(f"{self.county}: Zuercher API request failed: {exc}")
                    break
                if api_resp.status_code != 200:
                    logger.warning(
                        f"{self.county}: Zuercher API HTTP {api_resp.status_code} "
                        f"at {PORTAL_API_PATH} — portal may use legacy layout"
                    )
                    break
                try:
                    data = api_resp.json()
                except ValueError:
                    logger.warning(f"{self.county}: Zuercher API returned non-JSON")
                    break
                batch = data.get("records", []) if isinstance(data, dict) else []
                if total is None:
                    total = data.get("total_record_count") if isinstance(data, dict) else None
                inmates.extend(batch)
                if not batch or len(batch) < PORTAL_PAGE_SIZE:
                    break
                start += PORTAL_PAGE_SIZE
                if total is not None and start >= int(total):
                    break
                time.sleep(0.5)

            if not inmates:
                logger.warning(
                    f"Could not fetch inmates for {self.county} via Zuercher API. "
                    "Needs recon (portal offline or non-standard)."
                )
                return []

            records: List[ArrestRecord] = []
            st = getattr(self, "state", None) or "FL"
            for inmate in inmates:
                try:
                    raw_name = (inmate.get("name") or "").strip()
                    if not raw_name:
                        continue
                    if inmate.get("is_juvenile"):
                        continue  # never lead-score juveniles

                    first_name, middle_name, last_name = self._split_listed_name(raw_name)
                    full_name = raw_name.title() if raw_name.isupper() else raw_name

                    arrest_date = str(inmate.get("arrest_date") or "").strip()
                    hold_reasons = inmate.get("hold_reasons") or ""
                    charges, bond_cents = self._parse_hold_reasons(hold_reasons)
                    if not arrest_date:
                        m = _ARREST_DATE_RE.search(hold_reasons)
                        arrest_date = m.group(1) if m else ""

                    booking_num = str(
                        inmate.get("booking_number")
                        or inmate.get("BookingNumber")
                        or ""
                    ).strip() or self._stable_booking_number(raw_name, arrest_date)

                    record = ArrestRecord(
                        County=self.county,
                        State=st,
                        Full_Name=full_name,
                        First_Name=first_name,
                        Last_Name=last_name,
                        Middle_Name=middle_name,
                        Booking_Number=booking_num,
                        Booking_Date=arrest_date,
                        Charges=" | ".join(charges) if charges else "Unknown",
                        Bond_Amount=f"{bond_cents / 100:.2f}" if bond_cents > 0 else "0",
                        Status="In Custody",
                        Detail_URL=f"{base_url}/#/inmates",
                    )
                    records.append(record)
                except Exception as exc:
                    # Defensive parsing — one malformed record must not kill the run
                    logger.debug(f"{self.county}: skipped malformed Zuercher record: {exc}")
                    continue

            logger.info(
                f"✅ Found {len(records)} records for {self.county} "
                f"in {time.time() - start_time:.1f}s"
            )
            return records

        except Exception as e:
            logger.error(f"Error scraping Zuercher {self.county}: {e}")
            return []
