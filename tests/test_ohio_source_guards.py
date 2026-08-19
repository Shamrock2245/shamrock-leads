"""Regression tests for Ohio's registered-but-non-emitting pilot scopes."""
from __future__ import annotations

import inspect
import unittest

from scrapers.counties_oh.clermont import ClermontScraper
from scrapers.counties_oh.clinton import ClintonScraper
from core.scheduler import ScraperScheduler
from scrapers.counties_oh.huron import HuronScraper


class OhioSourceGuardTests(unittest.TestCase):
    SCRAPERS = (ClermontScraper, ClintonScraper, HuronScraper)

    def test_each_pilot_scope_is_explicitly_fail_closed(self) -> None:
        for scraper_cls in self.SCRAPERS:
            scraper = scraper_cls()
            self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
            self.assertEqual(scraper.state, "OH")
            self.assertTrue(scraper.OFFICIAL_SOURCE_URL.startswith("https://"))
            self.assertIn("approved public source contract", scraper.SOURCE_CONTRACT_REASON)

    def test_each_pilot_scope_emits_no_records_without_network(self) -> None:
        for scraper_cls in self.SCRAPERS:
            self.assertEqual(scraper_cls().scrape(), [])

    def test_base_runtime_stops_before_fetch_score_write_or_alert(self) -> None:
        for scraper_cls in self.SCRAPERS:
            result = scraper_cls().run(writers=[])
            self.assertEqual(result["source_contract_state"], "fail_closed")
            self.assertEqual(result["records_scraped"], 0)
            self.assertNotIn("writer_results", result)

    def test_scheduler_resolves_ohio_qualified_keys(self) -> None:
        scheduler = ScraperScheduler()
        for scraper_cls in self.SCRAPERS:
            scraper = scraper_cls()
            scheduler._scrapers[scraper.scraper_id] = scraper

        self.assertEqual(scheduler._resolve_job_id("oh_clermont"), "scraper_oh_clermont")
        self.assertEqual(scheduler._resolve_job_id("Clinton (OH)"), "scraper_oh_clinton")
        self.assertEqual(scheduler._resolve_job_id("huron_oh"), "scraper_oh_huron")

    def test_guarded_modules_do_not_include_unsafe_access_patterns(self) -> None:
        forbidden = ("create_stealth_session", "prefer_residential", "captcha", "selenium")
        for scraper_cls in self.SCRAPERS:
            source = inspect.getsource(scraper_cls).lower()
            self.assertFalse(any(token in source for token in forbidden))


if __name__ == "__main__":
    unittest.main()
