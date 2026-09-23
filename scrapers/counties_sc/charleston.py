"""
Charleston County (SC) Arrest Scraper.

Platform: Custom ASP.NET inmate search with Google reCAPTCHA v2
URL: https://inmatesearch.charlestoncounty.gov/
Results: ASP.NET ListView cards on results.aspx (not a GridView table)

Contract (validated 2026-09-23 non-writing Patchright smoke + live write):
- name: ``MainContent_ListViewMaster_lblfullname_N``
- source key: ``MainContent_ListViewMaster_lblInmateNumber_N`` (Inmate #)
- booking datetime: ``Label1_N`` (MM/DD/YYYY) + ``Label2_N`` (HH:MM)
- pagination: ``dpListView`` page submits (default 10/page)

Plain ``requests`` POSTs never clear reCAPTCHA and return an empty form — that
is why Health showed 0 Mongo writes. Synthetic ``CHS_<md5>`` keys are removed;
rows without a source Inmate # are dropped.

NOTE: changing ``ddnRcrdsPerPage`` postbacks wipe the result set (2026-09-23).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Set

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://inmatesearch.charlestoncounty.gov/"
SEARCH_DAYS = 7
MAX_PAGES = 25


class CharlestonScraper(BaseScraper):
    # Contract proven 2026-09-23: Inmate # + name + booking date/time + pager.
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "inmatesearch.charlestoncounty.gov ListView: Inmate #, name, booking "
        "date/time, charges; reCAPTCHA via Patchright audio solver; bounded pager."
    )

    @property
    def county(self) -> str:
        return "Charleston"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        try:
            html_pages = self._fetch_result_pages()
        except Exception as e:
            logger.error("Charleston scrape failed: %s", e)
            return []

        seen: Set[str] = set()
        records: List[ArrestRecord] = []
        for html in html_pages:
            for rec in self._parse_listview(html):
                key = rec.Booking_Number
                if not key or key in seen:
                    continue
                seen.add(key)
                records.append(rec)

        logger.info(
            "Charleston: %s records from %s page(s) in %.1fs",
            len(records),
            len(html_pages),
            time.time() - start_time,
        )
        return records

    def _fetch_result_pages(self) -> List[str]:
        """Browser search with reCAPTCHA; return HTML for each results page."""
        try:
            from patchright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("patchright required for Charleston reCAPTCHA path") from e

        from scrapers.chromium_flags import playwright_launch_kwargs
        from scrapers.recaptcha_audio_solver import RecaptchaAudioSolver

        now = datetime.now()
        start = now - timedelta(days=SEARCH_DAYS)
        date_fmt = "%m/%d/%Y"
        pages_html: List[str] = []

        with sync_playwright() as p:
            launch_kwargs = playwright_launch_kwargs(
                headless=True,
                channel="chrome",
                extra_args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception:
                launch_kwargs.pop("channel", None)
                browser = p.chromium.launch(**launch_kwargs)

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            try:
                page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_selector("iframe[src*='recaptcha']", timeout=15000)
                except Exception:
                    logger.debug("Charleston: recaptcha iframe wait timed out")
                page.wait_for_timeout(1000)

                page.fill("#txtBookDtFrom", start.strftime(date_fmt))
                page.fill("#txtBookDtTo", now.strftime(date_fmt))

                solver = RecaptchaAudioSolver(page)
                if not solver.solve():
                    logger.warning(
                        "Charleston: reCAPTCHA solve failed — returning empty "
                        "(needs Patchright + pydub/SpeechRecognition/ffmpeg)"
                    )
                    return []

                page.click("#MainContent_btnSearch")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(3000)

                if "results.aspx" not in (page.url or "").lower():
                    logger.warning(
                        "Charleston: search did not reach results.aspx (url=%s)",
                        page.url,
                    )
                    return []

                # NOTE: changing ddnRcrdsPerPage postbacks wipe the result set
                # (2026-09-23 smoke). Stay on default 10/page and walk dpListView.

                for page_idx in range(1, MAX_PAGES + 1):
                    html = page.content()
                    pages_html.append(html)
                    n_on_page = len(
                        re.findall(
                            r'id="MainContent_ListViewMaster_lblInmateNumber_\d+"',
                            html,
                        )
                    )
                    logger.info(
                        "Charleston results page=%s inmates_on_page=%s url=%s",
                        page_idx,
                        n_on_page,
                        page.url,
                    )
                    if n_on_page == 0:
                        break

                    next_btn = page.locator(
                        "#MainContent_dpListView input.page-number"
                        f"[value='{page_idx + 1}']"
                    )
                    if not next_btn.count():
                        break
                    next_btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(2500)
            finally:
                browser.close()

        return pages_html

    @classmethod
    def _parse_listview(cls, html: str) -> List[ArrestRecord]:
        """Parse ListView inmate cards; drop rows without source Inmate #."""
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        num_spans = soup.find_all(
            "span", id=re.compile(r"^MainContent_ListViewMaster_lblInmateNumber_\d+$")
        )
        for span in num_spans:
            m = re.search(r"_(\d+)$", span.get("id", ""))
            if not m:
                continue
            idx = m.group(1)
            inmate_num = span.get_text(" ", strip=True)
            if not inmate_num or not re.search(r"\d", inmate_num):
                continue

            name = cls._span_text(soup, f"MainContent_ListViewMaster_lblfullname_{idx}")
            if not name:
                continue
            book_date = cls._span_text(soup, f"MainContent_ListViewMaster_Label1_{idx}")
            book_time = cls._span_text(soup, f"MainContent_ListViewMaster_Label2_{idx}")
            status = cls._span_text(
                soup, f"MainContent_ListViewMaster_lblbookingstatus_{idx}"
            ) or "In Custody"
            age = cls._span_text(soup, f"MainContent_ListViewMaster_lblAge_{idx}")
            height = cls._span_text(soup, f"MainContent_ListViewMaster_lblHeight_{idx}")
            weight = cls._span_text(soup, f"MainContent_ListViewMaster_lblWeight_{idx}")
            sex = cls._span_text(soup, f"MainContent_ListViewMaster_lblGender_{idx}")
            race = cls._span_text(soup, f"MainContent_ListViewMaster_lblEthincity_{idx}")

            charges, bond = cls._parse_charges(soup, idx)
            first, middle, last = cls._split_name(name)
            booking_date = book_date
            if book_date and book_time:
                booking_date = f"{book_date} {book_time}".strip()

            records.append(
                ArrestRecord(
                    County="Charleston",
                    State="SC",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=inmate_num,
                    Booking_Date=booking_date,
                    Arrest_Date=book_date or booking_date,
                    Age_At_Arrest=age,
                    Height=height,
                    Weight=weight,
                    Sex=sex,
                    Race=race,
                    Charges=charges or "Unknown",
                    Bond_Amount=bond or "0",
                    Status=status,
                    Detail_URL=PORTAL_URL,
                    Facility="Charleston County Detention Center",
                )
            )
        return records

    @staticmethod
    def _span_text(soup: BeautifulSoup, element_id: str) -> str:
        el = soup.find(id=element_id)
        return el.get_text(" ", strip=True) if el else ""

    @staticmethod
    def _split_name(name: str) -> tuple[str, str, str]:
        if "," in name:
            last, rest = [p.strip() for p in name.split(",", 1)]
            parts = rest.split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
            return first, middle, last
        parts = name.split()
        if not parts:
            return "", "", ""
        if len(parts) == 1:
            return "", "", parts[0]
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_charges(soup: BeautifulSoup, idx: str) -> tuple[str, str]:
        """Collect charge descriptions and a total/max bond from nested ListView."""
        charge_uls = soup.find_all(
            id=re.compile(
                rf"^MainContent_ListViewMaster_lstviewchargedet_{idx}_ulchargedet_\d+$"
            )
        )
        descriptions: List[str] = []
        bonds: List[float] = []
        for ul in charge_uls:
            text = ul.get_text(" ", strip=True)
            m_desc = re.search(r"Charge Description:\s*(.+?)(?:\s*$)", text, re.I)
            if m_desc:
                descriptions.append(m_desc.group(1).strip())
            elif text:
                descriptions.append(text[:200])
            for bm in re.finditer(r"Bond Amount:\s*\$?\s*([\d,]+(?:\.\d+)?)", text, re.I):
                try:
                    bonds.append(float(bm.group(1).replace(",", "")))
                except ValueError:
                    pass

        tot = soup.find(
            id=f"MainContent_ListViewMaster_lstviewchargedet_{idx}_lblbondamttot_{idx}"
        )
        if tot:
            tm = re.search(r"([\d,]+(?:\.\d+)?)", tot.get_text(" ", strip=True))
            if tm:
                try:
                    bonds.append(float(tm.group(1).replace(",", "")))
                except ValueError:
                    pass

        bond_str = "0"
        if bonds:
            bond_str = f"{max(bonds):.2f}".rstrip("0").rstrip(".")
        charges = "; ".join(descriptions) if descriptions else ""
        return charges, bond_str
