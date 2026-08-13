import unittest
from unittest.mock import patch

from scrapers.counties_ga import eas_batch_runner


class EASBatchRunnerTests(unittest.TestCase):
    def test_eas_batch_is_fetch_only(self):
        events = []

        class FakeEASScraper:
            def __init__(self, county_name, slug):
                self.county_name = county_name
                self.slug = slug

            def scrape(self):
                events.append(("scrape", self.county_name, self.slug))
                return [self.county_name]

        with (
            patch.object(eas_batch_runner, "DynamicEASScraper", FakeEASScraper),
            patch.object(
                eas_batch_runner,
                "EAS_COUNTIES",
                [("Alpha", "alpha-ga"), ("Beta", "beta-ga")],
            ),
            patch.object(
                eas_batch_runner.time,
                "sleep",
                side_effect=lambda seconds: events.append(("sleep", seconds)),
            ),
        ):
            results = eas_batch_runner.run_eas_batch()

        self.assertEqual(results, ["Alpha", "Beta"])
        self.assertEqual(
            events,
            [
                ("scrape", "Alpha", "alpha-ga"),
                ("sleep", 2.0),
                ("scrape", "Beta", "beta-ga"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
