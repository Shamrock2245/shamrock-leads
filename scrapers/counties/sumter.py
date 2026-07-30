"""
Sumter County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Sumter County Sheriff's Office
URL: https://portal.sumtercountysheriff.org/smartwebclient/Jail.aspx
Method: curl_cffi POST to Jail.aspx/AddMoreResults (ASP.NET PageMethods AJAX)
Fix 2026-05-18: Replaced broken form POST with AJAX endpoint (same pattern as Suwannee/Putnam)
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://portal.sumtercountysheriff.org/smartwebclient"
AJAX_URL = f"{BASE_URL}/Jail.aspx/AddMoreResults"
FACILITY = "Sumter County Jail"
IMPERSONATE = "chrome131"
PAGE_SIZE = 185

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Jail.aspx",
    "Origin": "https://portal.sumtercountysheriff.org",
}


class SumterCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Sumter"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        session = cf.Session()
        session.headers.update({
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{BASE_URL}/Jail.aspx",
        })
        search_url = f"{BASE_URL}/Jail.aspx"

        # Establish ASP.NET session + cookies (AJAX without prior GET often 500s)
        try:
            resp = session.get(search_url, timeout=30, impersonate=IMPERSONATE, verify=False)
            if resp.status_code >= 500:
                logger.warning("Sumter: Jail.aspx GET %s — returning empty", resp.status_code)
                return []
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Sumter: initial GET failed (%s) — returning empty", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        def _hid(name: str) -> str:
            el = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
            return el["value"] if el and el.get("value") else ""

        # Primary: form POST with wildcard. Only send fields that exist on this host
        # (extra TypeSearch/Sort fields used by Putnam can break Sumter's postback).
        post_data = {
            "__VIEWSTATE": _hid("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hid("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hid("__EVENTVALIDATION"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txbLastName": "%",
            "txbFirstName": "",
            "txbMiddleName": "",
            "tbBeginDate": "",
            "tbEndDate": "",
            "tbBeginReleaseDate": "",
            "tbEndReleaseDate": "",
            "btnSumit": "Submit",
        }
        records: List[ArrestRecord] = []
        seen: set = set()
        try:
            resp2 = session.post(
                search_url, data=post_data, timeout=45, impersonate=IMPERSONATE, verify=False,
            )
            if resp2.status_code == 200 and len(resp2.text) > 1000:
                records = self._parse_html(resp2.text, seen)
                logger.info("Sumter: form POST returned %s records", len(records))
            else:
                logger.warning(
                    "Sumter: form POST status=%s len=%s",
                    getattr(resp2, "status_code", "?"),
                    len(getattr(resp2, "text", "") or ""),
                )
        except Exception as e:
            logger.warning("Sumter: form POST failed: %s", e)

        # AJAX pagination for remaining pages (when form path works)
        offset = len(records) if records else 0
        json_headers = {
            **HEADERS,
            "Referer": search_url,
            "Origin": "https://portal.sumtercountysheriff.org",
        }
        max_pages = 20
        page = 0
        while page < max_pages:
            payload = {
                "FirstName": "",
                "MiddleName": "",
                "LastName": "%",
                "BeginBookDate": "",
                "EndBookDate": "",
                "BeginReleaseDate": "",
                "EndReleaseDate": "",
                "TypeJailSearch": 0,
                "RecordsLoaded": offset,
                "SortOption": 1,
                "SortOrder": 1,
                "IsDefault": False,
            }
            try:
                r = session.post(
                    AJAX_URL,
                    json=payload,
                    headers=json_headers,
                    timeout=30,
                    impersonate=IMPERSONATE,
                    verify=False,
                )
                if r.status_code >= 500:
                    if not records:
                        logger.warning("Sumter: AJAX %s with no form results — empty", r.status_code)
                    break
                r.raise_for_status()
                data = r.json()
                d = data.get("d", data)
                if isinstance(d, dict):
                    d = d.get("Data", d)
                html_rows = d.get("data", "") if isinstance(d, dict) else ""
                results_returned = d.get("resultsReturned", 0) if isinstance(d, dict) else 0
            except Exception as e:
                logger.warning(f"Sumter AJAX failed (offset={offset}): {e}")
                break

            if not html_rows or results_returned == 0:
                break

            batch = self._parse_html(html_rows, seen)
            if not batch:
                break
            records.extend(batch)
            offset += results_returned or PAGE_SIZE
            page += 1

        logger.info(f"Sumter: {len(records)} records")
        return records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        """Shared SmartWeb card parser (handles short headers without DOB)."""
        from scrapers.smartweb_card_parser import parse_smartweb_cards

        return parse_smartweb_cards(
            html,
            county=self.county,
            facility=FACILITY,
            detail_url=f"{BASE_URL}/Jail.aspx",
            seen=seen,
            state="FL",
            log_prefix="Sumter",
        )
