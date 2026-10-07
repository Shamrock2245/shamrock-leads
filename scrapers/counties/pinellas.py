"""
Pinellas County Arrest Scraper — Who's In Jail (Blazor Server).
Source: Pinellas County Sheriff's Office
URL: https://whosinjail.pinellassheriff.gov/
Method: Patchright Chrome — date search + Next pagination.

HISTORY:
- v1: ASP.NET InmateBooking at pinellassheriff.gov/InmateBooking/ (ViewState POST)
- v2 (current): Old app pool returns HTTP 503. Site now points to Who's In Jail
  Blazor Server (SignalR; no public REST). Scrape via booking-date search.

Public roster covers current inmates + releases within ~30 days.

Bond / charges for hydrate:
- Roster rows include abbreviated charge text under the name when
  "Include Charge Information" is checked.
- Per-charge **Bond Assessed** and full **Offense Description** live only in
  the Subject Charge Report modal (name click). We sum Bond Assessed across
  charges; if the jail publishes $0.00 we store "0" (no invention).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Set

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.chromium_flags import playwright_launch_kwargs

logger = logging.getLogger(__name__)

SEARCH_URL = "https://whosinjail.pinellassheriff.gov/"
DAYS_BACK = 3  # Runs every 90 min — 3 days covers plenty of ground
MAX_PAGES_PER_DAY = 40
FACILITY = "Pinellas County Jail"


class PinellasCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pinellas"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        from patchright.sync_api import sync_playwright

        all_records: List[ArrestRecord] = []
        seen: Set[str] = set()

        with sync_playwright() as pw:
            # Prefer system Chrome when present (Mac / desktop smokes); fall back
            # to bundled Chromium on VPS images without channel="chrome".
            try:
                browser = pw.chromium.launch(
                    **playwright_launch_kwargs(channel="chrome")
                )
            except Exception as chrome_err:
                logger.warning(
                    "[Pinellas] channel=chrome failed (%s) — using bundled Chromium",
                    chrome_err,
                )
                browser = pw.chromium.launch(**playwright_launch_kwargs())

            try:
                # Plain context: Pinellas has no CF; stealth init-scripts have broken
                # DNS on some residential egress paths.
                page = browser.new_page()
                try:
                    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#booking-date", timeout=60000)
                    time.sleep(1)

                    for days_ago in range(DAYS_BACK):
                        target = datetime.now() - timedelta(days=days_ago)
                        date_iso = target.strftime("%Y-%m-%d")
                        try:
                            daily = self._scrape_date(page, date_iso, seen)
                            all_records.extend(daily)
                            logger.info(
                                "[Pinellas] %s: %d records", date_iso, len(daily)
                            )
                        except Exception as e:
                            logger.warning("[Pinellas] %s error: %s", date_iso, e)
                        time.sleep(1)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        logger.info("[Pinellas] Scraped %d total records", len(all_records))
        return all_records

    def _scrape_date(self, page, date_iso: str, seen: Set[str]) -> List[ArrestRecord]:
        """Search one booking date (HTML date input expects YYYY-MM-DD)."""
        page.fill("#booking-date", date_iso)
        # "Include Charge Information" — first checkbox on the form.
        try:
            page.locator("input[type=checkbox]").first.check(force=True)
        except Exception:
            pass

        page.get_by_role("button", name=re.compile(r"search", re.I)).click()
        # Empty days still render the shell; wait briefly for rows or settle.
        try:
            page.wait_for_selector("table tbody tr", timeout=20000)
        except Exception:
            logger.info("[Pinellas] %s: no result rows", date_iso)
            return []

        time.sleep(1.2)
        records: List[ArrestRecord] = []
        for page_num in range(1, MAX_PAGES_PER_DAY + 1):
            batch = self._extract_rows(page)
            new_count = 0
            for raw in batch:
                booking_num = (raw.get("booking_num") or "").strip()
                if not booking_num:
                    continue
                if booking_num in seen:
                    continue
                seen.add(booking_num)
                detail = self._read_detail_modal(page, booking_num)
                if detail:
                    if detail.get("charges"):
                        raw["charge"] = detail["charges"]
                    raw["bond_amount"] = detail.get("bond_amount") or "0"
                    if detail.get("case_numbers"):
                        raw["case_number"] = detail["case_numbers"]
                else:
                    # Roster-only path: charges may still be present; bond is
                    # modal-only — leave "0" rather than inventing an amount.
                    raw.setdefault("bond_amount", "0")
                rec = self._row_to_record(raw)
                if rec:
                    records.append(rec)
                    new_count += 1

            nxt = page.get_by_role("button", name=re.compile(r"^Next$", re.I))
            disabled = True
            if nxt.count():
                try:
                    disabled = nxt.is_disabled()
                except Exception:
                    disabled = True

            logger.debug(
                "[Pinellas] %s page %d: batch=%d new=%d next_disabled=%s",
                date_iso,
                page_num,
                len(batch),
                new_count,
                disabled,
            )
            if disabled or new_count == 0:
                break
            nxt.click()
            time.sleep(1.2)

        return records

    @staticmethod
    def _extract_rows(page) -> List[dict]:
        return page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('table tbody tr'));
              return rows.map(r => {
                const nameTd = r.querySelector('.td-name');
                const nameA = nameTd ? nameTd.querySelector('a') : null;
                const chargeSpan = nameTd ? nameTd.querySelector('span') : null;
                const race = r.querySelector('.td-race')?.innerText.trim() || '';
                const sex = r.querySelector('.td-sex')?.innerText.trim() || '';
                const dob = r.querySelector('.td-dob')?.innerText.trim() || '';
                const bookingTd = r.querySelector('.td-booking');
                const bookingParts = bookingTd
                  ? bookingTd.innerText.trim().split(/\\n+/).map(s => s.trim()).filter(Boolean)
                  : [];
                const tds = Array.from(r.querySelectorAll('td'));
                const last = tds[tds.length - 1];
                const ids = last
                  ? last.innerText.trim().split(/\\n+/).map(s => s.trim()).filter(Boolean)
                  : [];
                let name = '';
                if (nameA) {
                  name = nameA.innerText.trim();
                } else if (nameTd) {
                  name = nameTd.innerText.split('\\n')[0].trim();
                }
                return {
                  name,
                  charge: chargeSpan ? chargeSpan.innerText.trim() : '',
                  race, sex, dob,
                  booking_dt: bookingParts[0] || '',
                  custody: bookingParts[1] || '',
                  booking_num: ids[0] || '',
                  inmate_num: ids[1] || '',
                };
              });
            }"""
        )

    def _row_to_record(self, raw: dict) -> Optional[ArrestRecord]:
        name = self._clean(raw.get("name") or "")
        booking_num = self._clean(raw.get("booking_num") or "")
        if not name or not booking_num:
            return None

        first, middle, last = self._parse_name(name)
        booking_date, booking_time = self._parse_booking_dt(raw.get("booking_dt") or "")
        custody_raw = (raw.get("custody") or "").strip().lower()
        if "released" in custody_raw:
            status = "Released"
        elif custody_raw:
            status = "In Custody"
        else:
            status = "In Custody"

        sex_raw = (raw.get("sex") or "").strip().upper()
        sex = sex_raw[:1] if sex_raw else ""

        dob = self._normalize_dob(raw.get("dob") or "")
        inmate_num = self._clean(raw.get("inmate_num") or "")

        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=booking_num,
            Person_ID=inmate_num,
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=dob,
            Arrest_Date=booking_date,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Status=status,
            Facility=FACILITY,
            Race=self._clean(raw.get("race") or ""),
            Sex=sex,
            Charges=self._clean(raw.get("charge") or ""),
            Bond_Amount=self._format_bond_amount(raw.get("bond_amount") or "0"),
            Case_Number=self._clean(raw.get("case_number") or ""),
            Detail_URL=SEARCH_URL,
            LastCheckedMode="INITIAL",
        )


    def _read_detail_modal(self, page, booking_num: str) -> Optional[dict]:
        """Open Subject Charge Report for one roster row; parse bond + charges.

        Bond Assessed / Offense Description are not on the roster table — only
        in the name-click modal. Returns None if the modal does not render.
        """
        try:
            clicked = page.evaluate(
                """(bn) => {
                  for (const r of document.querySelectorAll('table tbody tr')) {
                    const tds = r.querySelectorAll('td');
                    if (!tds.length) continue;
                    const idText = (tds[tds.length - 1].innerText || '').trim();
                    const booking = idText.split(/\n/)[0].trim();
                    if (booking !== bn) continue;
                    const a = r.querySelector('.td-name a');
                    if (a) { a.click(); return true; }
                  }
                  return false;
                }""",
                booking_num,
            )
            if not clicked:
                return None
            # Blazor modal fills asynchronously
            deadline = time.time() + 8
            text = ""
            while time.time() < deadline:
                text = page.evaluate(
                    """() => {
                      for (const m of document.querySelectorAll('.modal, [role=dialog]')) {
                        const t = m.innerText || '';
                        if (/Bond Assessed/i.test(t)) return t;
                      }
                      return '';
                    }"""
                )
                if text:
                    break
                time.sleep(0.25)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            try:
                closer = page.get_by_role("button", name=re.compile(r"close", re.I))
                if closer.count():
                    closer.first.click(force=True)
            except Exception:
                pass
            time.sleep(0.35)
            if not text:
                return None
            return self.parse_charge_report_text(text)
        except Exception as exc:
            logger.debug("[Pinellas] detail modal %s: %s", booking_num, exc)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return None

    @staticmethod
    def parse_charge_report_text(text: str) -> dict:
        """Parse Subject Charge Report modal text (source-faithful, no invention).

        Sums every published **Bond Assessed** value. Uses Offense Description
        lines for Charges. Court Case Number values joined when present.
        """
        if not text:
            return {"charges": "", "bond_amount": "0", "case_numbers": ""}

        offenses = re.findall(
            r"Offense Description:\s*([^\n]+)", text, flags=re.I
        )
        bonds = re.findall(
            r"Bond Assessed:\s*([^\n]+)", text, flags=re.I
        )
        cases = re.findall(
            r"Court Case Number:\s*([^\n]+)", text, flags=re.I
        )

        total = 0.0
        saw_bond = False
        for raw in bonds:
            saw_bond = True
            total += PinellasCountyScraper._parse_bond_number(raw)

        charges = " | ".join(
            PinellasCountyScraper._clean(o) for o in offenses if o and o.strip()
        )
        case_numbers = " | ".join(
            PinellasCountyScraper._clean(c) for c in cases if c and c.strip()
        )

        if not saw_bond:
            bond_amount = "0"
        else:
            bond_amount = PinellasCountyScraper._format_bond_amount(total)

        return {
            "charges": charges,
            "bond_amount": bond_amount,
            "case_numbers": case_numbers,
        }

    @staticmethod
    def _parse_bond_number(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ("NOBOND", "NONE", "N/A", "HOLD", "ROR")):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _format_bond_amount(value) -> str:
        """Canonical Bond_Amount string; never invent — empty/invalid → '0'."""
        if value is None or value == "":
            return "0"
        if isinstance(value, (int, float)):
            amount = float(value)
        else:
            amount = PinellasCountyScraper._parse_bond_number(str(value))
        if amount <= 0:
            return "0"
        if amount.is_integer():
            return str(int(amount))
        return f"{amount:.2f}"

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name: str):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""

    @staticmethod
    def _parse_booking_dt(text: str):
        """Parse '9/22/2026 2:42:48 AM' → (YYYY-MM-DD, HH:MM:SS)."""
        text = (text or "").strip()
        if not text:
            return "", ""
        for fmt in (
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            except ValueError:
                continue
        # Fallback: date-only first token
        token = text.split()[0] if text else ""
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(token, fmt)
                return dt.strftime("%Y-%m-%d"), ""
            except ValueError:
                continue
        return text, ""

    @staticmethod
    def _normalize_dob(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%m/%d/%Y")
            except ValueError:
                continue
        return text
