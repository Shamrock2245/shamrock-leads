from unittest.mock import patch

from scripts.inspect_public_source_contract import inspect


class _JsonResponse:
    url = "https://example.test/api/roster"
    status_code = 200
    headers = {"content-type": "application/json"}
    text = ""

    @staticmethod
    def json():
        return [{"inmate_id": "source-id-not-emitted", "booking_date": "2026-01-01"}]


def test_json_inspection_reports_schema_without_roster_values():
    with patch("requests.get", return_value=_JsonResponse()):
        result = inspect("https://example.test/api/roster")

    assert result == {
        "request_url": "https://example.test/api/roster",
        "final_origin": "https://example.test",
        "status_code": 200,
        "content_type": "application/json",
        "record_schema_fields": ["booking_date", "inmate_id"],
    }
