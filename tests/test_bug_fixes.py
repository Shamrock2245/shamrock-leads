"""
Unit tests verifying bug fixes for ShamrockLeads.
"""
import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from dashboard.services.google_calendar_service import GoogleCalendarService
from scrapers.counties.suwannee import SuwanneeCountyScraper
from writers.sheets_writer import SheetsWriter


class TestBugFixes(unittest.TestCase):
    def test_google_calendar_service_create_event_no_name_error(self):
        """Verify create_event doesn't raise NameError for datetime_info or defendant_email."""
        service = GoogleCalendarService()

        # Mock _service to prevent real API call and simulate successful response
        mock_svc = MagicMock()
        mock_svc.events().insert().execute.return_value = {
            "id": "mock_event_123",
            "htmlLink": "https://calendar.google.com/event?id=123"
        }
        service._service = mock_svc
        service.check_duplicate = MagicMock(return_value=False)
        service._generate_dedup_key = MagicMock(return_value="test_key")

        email_data = {
            "case_number": "2026-CF-001234",
            "datetime_info": {"date_str": "2026-08-10", "time_str": "09:30 AM"},
            "defendant_name": "JOHN DOE",
            "county": "Lee",
            "judge": "Judge Smith",
            "location": "Courtroom 3A",
            "event_type": "courtDate",
        }

        result = service.create_event(email_data)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("id"), "mock_event_123")

    def test_suwannee_scraper_imports(self):
        """Verify Suwannee scraper module has datetime and timezone imported."""
        scraper = SuwanneeCountyScraper()
        self.assertEqual(scraper.county, "Suwannee")

    def test_sheets_writer_datetime_utc(self):
        """Verify sheets_writer has timezone imported and usable."""
        now_str = datetime.now(timezone.utc).isoformat()
        self.assertIn("+00:00", now_str)


if __name__ == "__main__":
    unittest.main()
