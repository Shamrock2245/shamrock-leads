"""
Sarasota County Arrest Scraper
==============================
Primary:  mugshotssarasota.com WordPress API
          Third-party mirror of Sarasota County Jail bookings (live daily posts).
          Official sources are currently unusable (see below).

Fallback A: JailTracker Blazor WASM (omsweb.public-safety-cloud.com)
            SARASOTA_COUNTY_FL — captcha solvable, but FL Offender POST returns
            HTTP 400 after valid captcha (agency backend issue, Jul 2026).
            Kept so we recover automatically if JailTracker FL is fixed.

Fallback B: Revize CMS (cms.revize.com iframe on sarasotasheriff.org)
            Hard-blocked by Cloudflare WAF on residential + proxy exits.

HISTORY:
- v1: JailTracker Blazor — abandoned after global 400s
- v2: Revize CMS via Playwright + office SOCKS
- v3: APE residential + Patchright for Revize
- v4: JailTracker primary again (site live); Revize fallback
- v5: mugshotssarasota.com primary; JT + Revize fallbacks
- v6 (current): mugshots path on APE StealthSession (curl_cffi + residential)
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from scrapers.base_scraper import BaseScraper
from scrapers.jailtracker_base import JailTrackerBaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── JailTracker ──
JT_COUNTY_ID = "SARASOTA_COUNTY_FL"
FACILITY = "Sarasota County Jail"

# ── mugshotssarasota.com (primary working source) ──
MUGSHOTS_API = "https://mugshotssarasota.com/wp-json/wp/v2/posts"
MUGSHOTS_CATEGORY = 2  # "Sarasota County Jail Arrest Booking Inquiry"
MUGSHOTS_LOOKBACK_DAYS = 7
MUGSHOTS_MAX_PAGES = 8
MUGSHOTS_PER_PAGE = 50

# ── Revize CMS (last-resort fallback) ──
REVIZE_BASE = "https://cms.revize.com/revize/apps/sarasota"
MAIN_URL = f"{REVIZE_BASE}/index.php"
DETAIL_DELAY_S = 1.0
MAX_INMATES = 1500
PAGE_LOAD_TIMEOUT = 60000


class SarasotaCountyScraper(JailTrackerBaseScraper):
    """Sarasota FL — mugshots primary, JailTracker + Revize fallbacks."""

    county_jt_id = JT_COUNTY_ID
    facility_name = FACILITY

    @property
    def county(self) -> str:
        return "Sarasota"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        # ── Path A: mugshotssarasota.com (working official mirror) ──
        try:
            logger.info("[Sarasota] Attempting mugshotssarasota.com path…")
            records = self._scrape_mugshots()
            if records:
                logger.info(
                    "[Sarasota] Mugshots success: %d records", len(records)
                )
                return records
            logger.warning("[Sarasota] Mugshots returned 0 records")
        except Exception as e:
            logger.warning("[Sarasota] Mugshots failed (%s)", e)

        # ── Path B: JailTracker (may 400 on FL agencies) ──
        try:
            logger.info("[Sarasota] Attempting JailTracker path…")
            records = super().scrape()
            if records:
                logger.info(
                    "[Sarasota] JailTracker success: %d records", len(records)
                )
                for r in records:
                    if not getattr(r, "State", None):
                        r.State = "FL"
                    if not getattr(r, "Facility", None):
                        r.Facility = FACILITY
                return records
            logger.warning(
                "[Sarasota] JailTracker returned 0 records — trying Revize fallback"
            )
        except Exception as e:
            logger.warning(
                "[Sarasota] JailTracker failed (%s) — trying Revize fallback", e
            )

        # ── Path C: Revize CMS (residential browser) ──
        return self._scrape_revize()

    # ──────────────────────────────────────────────────────────────
    # Path A: mugshotssarasota.com WordPress REST API
    # Uses APE StealthSession (curl_cffi Chrome JA3 + residential failover)
    # ──────────────────────────────────────────────────────────────
    def _scrape_mugshots(self) -> List[ArrestRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MUGSHOTS_LOOKBACK_DAYS)
        # Site headers only — TLSFingerprinter owns User-Agent
        headers = {
            "Accept": "application/json",
            "Referer": "https://mugshotssarasota.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        records: List[ArrestRecord] = []
        seen_bookings: set = set()

        session = self._open_stealth_session(sticky="fl-sarasota-mugshots")
        try:
            for page in range(1, MUGSHOTS_MAX_PAGES + 1):
                params = {
                    "per_page": MUGSHOTS_PER_PAGE,
                    "page": page,
                    "categories": MUGSHOTS_CATEGORY,
                    "orderby": "date",
                    "order": "desc",
                    "_fields": (
                        "id,date,title,link,slug,"
                        "yoast_head_json,jetpack_featured_media_url"
                    ),
                }
                resp = self._stealth_get(
                    session, MUGSHOTS_API, params=params, headers=headers
                )
                if resp is None:
                    break
                if resp.status_code == 400:
                    # WP returns 400 when page is out of range
                    break
                if resp.status_code != 200:
                    logger.warning(
                        "[Sarasota] Mugshots HTTP %s on page %d",
                        resp.status_code,
                        page,
                    )
                    if resp.status_code in (403, 429, 503) and hasattr(
                        session, "rotate_proxy"
                    ):
                        session.rotate_proxy()
                    break
                posts = resp.json()
                if not isinstance(posts, list) or not posts:
                    break

                page_had_recent = False
                for post in posts:
                    post_date = self._parse_iso_date(post.get("date") or "")
                    if post_date and post_date < cutoff.replace(tzinfo=None):
                        continue
                    page_had_recent = True
                    rec = self._parse_mugshots_post(post)
                    if not rec:
                        continue
                    key = rec.Booking_Number or rec.Detail_URL
                    if key in seen_bookings:
                        continue
                    seen_bookings.add(key)
                    records.append(rec)

                logger.info(
                    "[Sarasota] Mugshots page %d: +posts, total records=%d",
                    page,
                    len(records),
                )
                if not page_had_recent:
                    break
                # Respect the API lightly
                time.sleep(0.25 + random.uniform(0, 0.35))
        finally:
            self._close_stealth_session(session)

        logger.info(
            "[Sarasota] Mugshots scraped %d records (lookback=%dd)",
            len(records),
            MUGSHOTS_LOOKBACK_DAYS,
        )
        return records

    def _open_stealth_session(self, sticky: str):
        """APE StealthSession preferred; plain curl_cffi Session as fallback."""
        try:
            from scrapers.proxy_engine import create_stealth_session

            sess = create_stealth_session(
                sticky_session_id=sticky,
                prefer_residential=True,
                allow_direct=True,
                timeout=30,
                impersonate="chrome131",
            )
            proxy = getattr(sess, "proxy", None) or "direct"
            logger.info(
                "[Sarasota] StealthSession ready (proxy=%s)",
                (proxy[:60] if isinstance(proxy, str) else proxy),
            )
            return sess
        except Exception as exc:
            logger.warning(
                "[Sarasota] StealthSession unavailable (%s) — curl_cffi fallback",
                exc,
            )
        try:
            from curl_cffi import requests as cffi_requests

            sess = cffi_requests.Session()
            proxy = None
            if getattr(self, "ape", None):
                try:
                    proxy = self.get_proxy(prefer_residential=True)
                except Exception:
                    proxy = None
            if proxy:
                sess.proxies = {"http": proxy, "https": proxy}
            sess._shamrock_impersonate = "chrome131"  # type: ignore[attr-defined]
            return sess
        except ImportError:
            import httpx

            return httpx.Client(timeout=30, follow_redirects=True)

    @staticmethod
    def _close_stealth_session(session) -> None:
        if session is None:
            return
        try:
            if hasattr(session, "close"):
                session.close()
        except Exception:
            pass

    def _stealth_get(self, session, url: str, *, params=None, headers=None):
        """GET that works with StealthSession, curl_cffi Session, or httpx."""
        # StealthSession: rotate + JA3 built-in
        if hasattr(session, "rotate_proxy") and hasattr(session, "request"):
            return session.get(
                url, params=params, headers=headers, max_retries=3, timeout=30
            )
        # curl_cffi Session
        if hasattr(session, "request") and hasattr(session, "proxies"):
            kwargs = {"params": params, "headers": headers, "timeout": 30}
            imp = getattr(session, "_shamrock_impersonate", None)
            if imp:
                kwargs["impersonate"] = imp
            return session.get(url, **kwargs)
        # httpx Client
        return session.get(url, params=params, headers=headers)

    def _parse_mugshots_post(self, post: dict) -> Optional[ArrestRecord]:
        """Parse a WP post into ArrestRecord using yoast SEO fields + image URL."""
        try:
            title = unescape((post.get("title") or {}).get("rendered") or "").strip()
            # "JARON MOSS booked for No Bond" → name
            full_name = re.sub(
                r"\s+booked\s+for\s+.*$", "", title, flags=re.I
            ).strip()
            if not full_name or len(full_name) < 3:
                return None

            yoast = post.get("yoast_head_json") or {}
            desc = (
                yoast.get("description")
                or yoast.get("og_description")
                or ""
            )
            img = post.get("jetpack_featured_media_url") or ""
            if not img:
                og_imgs = yoast.get("og_image") or []
                if og_imgs and isinstance(og_imgs[0], dict):
                    img = og_imgs[0].get("url") or ""

            # Booking number from mugshot filename: NAME-202600006744-s13.jpg
            booking = ""
            bm = re.search(r"-(\d{10,})(?:-s\d+)?\.(?:jpg|jpeg|png|webp)", img, re.I)
            if bm:
                booking = bm.group(1)
            if not booking:
                bm = re.search(r"(\d{10,})", img)
                if bm:
                    booking = bm.group(1)
            if not booking:
                booking = f"MS-{post.get('id', '')}"

            # "NAME - age44 arrested on 20260716 for CHARGE:. Bail $500.00."
            age = ""
            arrest_date = ""
            charges = ""
            bond_raw = ""
            m = re.search(r"age\s*(\d+)", desc, re.I)
            if m:
                age = m.group(1)
            m = re.search(r"arrested on (\d{8})", desc, re.I)
            if m:
                d = m.group(1)
                arrest_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            m = re.search(
                r"for\s+(.+?)\.?\s*Bail\s+(.+?)\.?\s*$", desc, re.I | re.S
            )
            if m:
                charges = m.group(1).strip(" .:")
                bond_raw = m.group(2).strip(" .")
            else:
                # Title fallback: "NAME booked for $500.00" / "booked for No Bond"
                m = re.search(r"booked\s+for\s+(.+)$", title, re.I)
                if m:
                    bond_raw = m.group(1).strip()

            if not arrest_date:
                arrest_date = self._parse_iso_date_str(post.get("date") or "") or ""

            bond_amount, bond_type = self._split_bond(bond_raw)
            first, middle, last = self._split_name(full_name)
            # City from slug: jaron-moss-of-sarasota → sarasota
            city = ""
            slug = post.get("slug") or ""
            cm = re.search(r"-of-([a-z0-9-]+)/?$", slug)
            if cm:
                city = cm.group(1).replace("-", " ").title()

            detail_url = post.get("link") or ""
            status = "In Custody"

            return ArrestRecord(
                County="Sarasota",
                State="FL",
                Booking_Number=booking,
                Full_Name=f"{last}, {first}".strip(", ") if last else full_name,
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                Age_At_Arrest=age,
                Arrest_Date=arrest_date,
                Booking_Date=arrest_date,
                Status=status,
                Facility=FACILITY,
                City=city,
                Mugshot_URL=img,
                Charges=charges,
                Bond_Amount=bond_amount,
                Bond_Type=bond_type,
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            )
        except Exception as e:
            logger.warning("[Sarasota] mugshots parse error: %s", e)
            return None

    @staticmethod
    def _split_name(full: str) -> tuple:
        """Parse 'FIRST MIDDLE LAST' or 'LAST, FIRST' into parts."""
        full = (full or "").strip()
        if not full:
            return "", "", ""
        if "," in full:
            parts = full.split(",", 1)
            last = parts[0].strip()
            rest = parts[1].strip().split()
            first = rest[0] if rest else ""
            middle = " ".join(rest[1:]) if len(rest) > 1 else ""
            return first, middle, last
        tokens = full.split()
        if len(tokens) == 1:
            return tokens[0], "", ""
        if len(tokens) == 2:
            return tokens[0], "", tokens[1]
        return tokens[0], " ".join(tokens[1:-1]), tokens[-1]

    @staticmethod
    def _split_bond(raw: str) -> tuple:
        """Return (bond_amount_str, bond_type)."""
        raw = (raw or "").strip()
        if not raw:
            return "0", ""
        upper = raw.upper().replace("$", "").strip()
        if "NO BOND" in upper or upper in ("NOBOND", "NONE", "HOLD"):
            return "0", "No Bond"
        if "ROR" in upper or "RELEASE" in upper:
            return "0", raw
        cleaned = re.sub(r"[$,\s]", "", raw)
        m = re.search(r"([\d]+(?:\.\d+)?)", cleaned)
        if m:
            try:
                val = float(m.group(1))
                return (str(int(val)) if val == int(val) else str(val)), "Surety"
            except ValueError:
                pass
        return "0", raw

    @staticmethod
    def _parse_iso_date(s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            # 2026-07-16T18:26:03
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    @staticmethod
    def _parse_iso_date_str(s: str) -> Optional[str]:
        dt = SarasotaCountyScraper._parse_iso_date(s)
        return dt.strftime("%Y-%m-%d") if dt else None

    # ──────────────────────────────────────────────────────────────
    # Path C: Revize (last resort — usually CF-blocked)
    # ──────────────────────────────────────────────────────────────
    def _scrape_revize(self) -> List[ArrestRecord]:
        from scrapers.socks_proxy import resolve_residential_proxy
        from scrapers.cf_browser import (
            launch_cf_browser,
            new_stealth_context,
            wait_past_cloudflare,
        )

        proxy_url, proxy_source = resolve_residential_proxy(
            self, sticky_session="fl-sarasota-revize"
        )
        if proxy_source == "ape":
            try:
                from scrapers.cf_browser import check_exit_ip

                direct = check_exit_ip(None, timeout=10, retries=1)
                if direct.get("residential_likely"):
                    logger.info(
                        "[Sarasota] Using DIRECT residential for Revize "
                        "(avoids proxy WAF brand)"
                    )
                    proxy_url, proxy_source = None, "direct"
            except Exception:
                pass

        logger.info("[Sarasota] Revize path proxy_source=%s", proxy_source)
        pw = browser = None
        t0 = time.time()
        try:
            pw, browser, engine = launch_cf_browser(
                proxy_url,
                label="Sarasota",
                verify_residential=(proxy_source != "direct"),
            )
            context = new_stealth_context(browser)
            page = context.new_page()

            entry_urls = [
                "https://www.sarasotasheriff.org/arrest-reports/index.php",
                MAIN_URL,
            ]
            roster: list = []
            for entry in entry_urls:
                logger.info(
                    "[Sarasota] Phase 1: Loading %s (engine=%s)", entry, engine
                )
                page.goto(
                    entry, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT
                )
                if not wait_past_cloudflare(
                    page, label=f"Sarasota {entry[-40:]}", max_wait=50
                ):
                    title = (page.title() or "").lower()
                    if "blocked" in title or "attention required" in title:
                        logger.error("[Sarasota] Cloudflare hard-block on %s", entry)
                        continue
                    logger.warning("[Sarasota] CF not cleared on %s", entry)
                    continue

                roster = self._collect_roster_links(page)
                if not roster:
                    for frame in page.frames:
                        if "revize" in (frame.url or "").lower() or "viewInmate" in (
                            frame.url or ""
                        ):
                            try:
                                roster = self._collect_roster_links(frame)
                                if roster:
                                    page = frame  # type: ignore
                                    break
                            except Exception:
                                continue
                if roster:
                    break

            if not roster:
                logger.error(
                    "[Sarasota] No inmate links found (Revize CF-blocked). "
                    "Mugshots + JailTracker also empty."
                )
                if proxy_source == "ape" and proxy_url:
                    self.record_proxy_failure(proxy_url)
                return []

            logger.info("[Sarasota] Found %d inmates in roster", len(roster))

            inmates: List[Dict[str, str]] = []
            for entry in roster[:MAX_INMATES]:
                parsed = self._parse_roster_entry(entry["text"], entry["href"])
                if parsed:
                    inmates.append(parsed)
            logger.info("[Sarasota] Parsed %d roster entries", len(inmates))

            records: List[ArrestRecord] = []
            seen_bookings: set = set()
            for i, inmate in enumerate(inmates):
                try:
                    detail_records = self._extract_detail(
                        page, inmate, seen_bookings
                    )
                    records.extend(detail_records)
                    if (i + 1) % 50 == 0:
                        logger.info(
                            "[Sarasota] Progress: %d/%d inmates, %d records",
                            i + 1,
                            len(inmates),
                            len(records),
                        )
                    time.sleep(DETAIL_DELAY_S)
                except Exception as e:
                    logger.warning(
                        "[Sarasota] Error on inmate %s: %s",
                        inmate.get("name", "?"),
                        e,
                    )
                    continue

            logger.info(
                "[Sarasota] Revize scraped %d records from %d inmates "
                "(proxy=%s, engine=%s)",
                len(records),
                len(inmates),
                proxy_source,
                engine,
            )
            if records and proxy_source == "ape" and proxy_url:
                self.record_proxy_success(proxy_url, (time.time() - t0) * 1000)
            return records

        except Exception as e:
            logger.error("[Sarasota] Revize fatal: %s", e)
            if proxy_source == "ape" and proxy_url:
                try:
                    self.record_proxy_failure(proxy_url)
                except Exception:
                    pass
            raise
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

    @staticmethod
    def _collect_roster_links(page) -> list:
        try:
            btn = page.query_selector(
                "button.dropdown-toggle, .dropdown-toggle, [data-toggle='dropdown']"
            )
            if btn:
                try:
                    btn.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            return (
                page.evaluate(
                    """() => {
                    const links = document.querySelectorAll('a[href*="viewInmate"]');
                    return Array.from(links).map(a => ({
                        text: (a.textContent || '').trim(),
                        href: a.href,
                    })).filter(x => x.href);
                }"""
                )
                or []
            )
        except Exception:
            return []

    @staticmethod
    def _parse_roster_entry(text: str, href: str) -> Optional[Dict[str, str]]:
        if not text or "viewInmate" not in href:
            return None
        id_match = re.search(r"id=(\d+)", href)
        if not id_match:
            return None
        inmate_id = id_match.group(1)
        parts = text.rsplit(" - ", 1)
        name_part = parts[0].strip()
        dob_part = parts[1].strip() if len(parts) > 1 else ""
        name_pieces = name_part.split(",", 1)
        last_name = name_pieces[0].strip()
        rest = name_pieces[1].strip() if len(name_pieces) > 1 else ""
        first_name = middle_name = ""
        if rest:
            tokens = rest.split()
            first_name = tokens[0] if tokens else ""
            middle_name = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        full_name = f"{last_name}, {first_name}"
        if middle_name:
            full_name = f"{last_name}, {first_name} {middle_name}"
        return {
            "inmate_id": inmate_id,
            "name": name_part,
            "full_name": full_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "dob": dob_part,
            "detail_url": href,
        }

    def _extract_detail(
        self, page: Any, inmate: Dict[str, str], seen_bookings: set
    ) -> List[ArrestRecord]:
        detail_url = inmate["detail_url"]
        inmate_id = inmate["inmate_id"]
        try:
            page.goto(
                detail_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT
            )
            page.wait_for_timeout(1500)
            title = (page.title() or "").lower()
            if "just a moment" in title or "attention required" in title:
                page.wait_for_timeout(8000)
                title = (page.title() or "").lower()
                if "just a moment" in title or "blocked" in title:
                    return []

            data = page.evaluate(
                """() => {
                const result = { personal: {}, charges: [] };
                const allText = document.body ? document.body.innerText : '';
                const lines = allText.split('\\n').map(l => l.trim()).filter(Boolean);
                const personalFields = ['PIN:', 'Date of Birth:', 'Race:', 'Sex:', 'Location:'];
                for (let i = 0; i < lines.length; i++) {
                    for (const field of personalFields) {
                        if (lines[i] === field && i + 1 < lines.length) {
                            result.personal[field.replace(':', '')] = lines[i + 1];
                        }
                    }
                }
                const tables = document.querySelectorAll('table');
                for (const table of tables) {
                    const rows = table.querySelectorAll('tr');
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td'));
                        if (cells.length >= 8) {
                            result.charges.push({
                                booking_number: cells[0]?.textContent?.trim() || '',
                                offense: cells[1]?.textContent?.trim() || '',
                                counts: cells[2]?.textContent?.trim() || '',
                                arraign_date: cells[3]?.textContent?.trim() || '',
                                bond_amount: cells[4]?.textContent?.trim() || '',
                                bond_type: cells[5]?.textContent?.trim() || '',
                                intake_datetime: cells[6]?.textContent?.trim() || '',
                                case_number: cells[7]?.textContent?.trim() || '',
                                release_datetime: cells[8]?.textContent?.trim() || '',
                                hold: cells[9]?.textContent?.trim() || '',
                            });
                        }
                    }
                }
                return result;
            }"""
            )
            if not data:
                return []

            personal = data.get("personal", {})
            charges_list = data.get("charges", [])
            race = personal.get("Race", "")
            sex = personal.get("Sex", "")
            pin = personal.get("PIN", inmate_id)
            records: List[ArrestRecord] = []

            if charges_list:
                bookings: Dict[str, List[Dict]] = {}
                for charge in charges_list:
                    bn = charge.get("booking_number", "").strip() or pin
                    bookings.setdefault(bn, []).append(charge)

                for booking_num, charges in bookings.items():
                    dedup_key = f"Sarasota:{booking_num}"
                    if dedup_key in seen_bookings:
                        continue
                    seen_bookings.add(dedup_key)

                    charge_descriptions = []
                    total_bond = 0.0
                    bond_type = intake_datetime = case_number = arraign_date = ""
                    release_datetime = ""
                    has_hold = False
                    for c in charges:
                        offense = c.get("offense", "").strip()
                        if offense:
                            charge_descriptions.append(offense)
                        bond_val = self._parse_bond_amount(c.get("bond_amount", ""))
                        if bond_val is not None:
                            total_bond += bond_val
                        bt = c.get("bond_type", "").strip()
                        if bt and not bond_type:
                            bond_type = bt
                        idt = c.get("intake_datetime", "").strip()
                        if idt and not intake_datetime:
                            intake_datetime = idt
                        cn = c.get("case_number", "").strip()
                        if cn and not case_number:
                            case_number = cn
                        ad = c.get("arraign_date", "").strip()
                        if ad and not arraign_date:
                            arraign_date = ad
                        rd = c.get("release_datetime", "").strip()
                        if rd and not release_datetime:
                            release_datetime = rd
                        if c.get("hold", "").strip().upper() == "Y":
                            has_hold = True

                    status = "Released" if release_datetime else "In Custody"
                    booking_date, booking_time = self._parse_datetime(intake_datetime)
                    if "No Bond" in str(charges) or has_hold:
                        bond_type = bond_type or "No Bond"

                    records.append(
                        ArrestRecord(
                            County="Sarasota",
                            State="FL",
                            Booking_Number=booking_num,
                            Person_ID=pin,
                            Full_Name=inmate["full_name"],
                            First_Name=inmate["first_name"],
                            Middle_Name=inmate["middle_name"],
                            Last_Name=inmate["last_name"],
                            DOB=self._parse_date(inmate["dob"]) or "",
                            Arrest_Date=booking_date
                            or self._parse_date(arraign_date)
                            or "",
                            Booking_Date=booking_date or "",
                            Booking_Time=booking_time or "",
                            Status=status,
                            Release_Date=self._parse_datetime(release_datetime)[0]
                            if release_datetime
                            else "",
                            Facility=FACILITY,
                            Race=race,
                            Sex=sex,
                            Charges=" | ".join(charge_descriptions),
                            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                            Bond_Type=bond_type,
                            Case_Number=case_number,
                            Court_Date=self._parse_date(arraign_date) or "",
                            Detail_URL=detail_url,
                        )
                    )
            else:
                dedup_key = f"Sarasota:{pin}"
                if dedup_key not in seen_bookings:
                    seen_bookings.add(dedup_key)
                    records.append(
                        ArrestRecord(
                            County="Sarasota",
                            State="FL",
                            Booking_Number=pin,
                            Person_ID=pin,
                            Full_Name=inmate["full_name"],
                            First_Name=inmate["first_name"],
                            Middle_Name=inmate["middle_name"],
                            Last_Name=inmate["last_name"],
                            DOB=self._parse_date(inmate["dob"]) or "",
                            Status="In Custody",
                            Facility=FACILITY,
                            Race=race,
                            Sex=sex,
                            Detail_URL=detail_url,
                        )
                    )
            return records
        except Exception as e:
            logger.warning("[Sarasota] detail extract %s: %s", inmate_id, e)
            return []

    @staticmethod
    def _parse_bond_amount(s: str) -> Optional[float]:
        if not s:
            return None
        cleaned = re.sub(r"[$,\s]", "", s.strip().upper())
        if not cleaned or cleaned in ("NOBOND", "NONE", "N/A", "HOLD", "-"):
            return 0.0 if cleaned in ("NOBOND", "HOLD") else None
        m = re.search(r"([\d]+(?:\.\d+)?)", cleaned)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[str]:
        if not text:
            return None
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"):
            try:
                return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_datetime(text: str):
        if not text:
            return None, None
        text = text.strip()
        for fmt in (
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %H:%M",
            "%m-%d-%Y %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
            except ValueError:
                continue
        d = SarasotaCountyScraper._parse_date(text)
        return d, None
