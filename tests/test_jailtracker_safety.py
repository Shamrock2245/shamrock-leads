import importlib
import inspect
import unittest

from scrapers.jailtracker_base import JailTrackerBaseScraper


AUDITED_WRAPPERS = [
    ("scrapers.counties.baker", "BakerCountyScraper"),
    ("scrapers.counties.calhoun", "CalhounCountyScraper"),
    ("scrapers.counties.gulf", "GulfCountyScraper"),
    ("scrapers.counties.holmes", "HolmesCountyScraper"),
    ("scrapers.counties.levy", "LevyCountyScraper"),
    ("scrapers.counties.wakulla", "WakullaCountyScraper"),
    ("scrapers.counties.washington", "WashingtonCountyScraper"),
    ("scrapers.counties_al.shelby", "ShelbyScraper"),
    ("scrapers.counties_ga.dawson", "DawsonScraper"),
    ("scrapers.counties_ga.gordon", "GordonScraper"),
    ("scrapers.counties_ga.pickens", "PickensScraper"),
    ("scrapers.counties_ga.walker", "WalkerScraper"),
    ("scrapers.counties_ga.whitfield", "WhitfieldScraper"),
    ("scrapers.counties_ms.desoto", "DeSotoScraper"),
    ("scrapers.counties_ms.jones", "JonesScraper"),
    ("scrapers.counties_ms.lauderdale", "LauderdaleScraper"),
    ("scrapers.counties_ms.madison", "MadisonMSScraper"),
    ("scrapers.counties_sc.chester", "ChesterScraper"),
    ("scrapers.counties_sc.greenwood", "GreenwoodScraper"),
    ("scrapers.counties_tn.blount", "BlountScraper"),
    ("scrapers.counties_tn.maury", "MauryScraper"),
    ("scrapers.counties_tn.rutherford", "RutherfordScraper"),
    ("scrapers.counties_tn.williamson", "WilliamsonScraper"),
    ("scrapers.counties_tn.wilson", "WilsonScraper"),
]


class TestJailTrackerSafety(unittest.TestCase):
    def test_base_contract_is_explicitly_unverified(self):
        self.assertFalse(JailTrackerBaseScraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("human verification", JailTrackerBaseScraper.SOURCE_CONTRACT_REASON)

    def test_audited_wrappers_fail_closed_without_network(self):
        for module_name, class_name in AUDITED_WRAPPERS:
            with self.subTest(wrapper=f"{module_name}.{class_name}"):
                scraper_class = getattr(importlib.import_module(module_name), class_name)
                self.assertTrue(issubclass(scraper_class, JailTrackerBaseScraper))
                self.assertIs(scraper_class.scrape, JailTrackerBaseScraper.scrape)
                self.assertFalse(scraper_class.SOURCE_CONTRACT_VALIDATED)
                self.assertEqual(scraper_class().scrape(), [])

    def test_prohibited_automation_and_sensitive_paths_are_absent(self):
        source = inspect.getsource(JailTrackerBaseScraper)
        for prohibited in (
            "playwright",
            "captcha",
            "ocr",
            "solvecaptcha",
            "openai",
            "photoUrl",
            "mugshot",
            "DOB",
            "page.goto",
        ):
            self.assertNotIn(prohibited.lower(), source.lower())


if __name__ == "__main__":
    unittest.main()
