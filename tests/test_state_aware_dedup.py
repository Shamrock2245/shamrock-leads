"""Regression tests — state-aware idempotent writes + new-record broadcast flow.

July 2026: multi-state expansion introduced county-name collisions
(Lee FL/GA/SC, Sumter FL/GA/SC, ...). These tests lock the natural key at
(state, county, booking_number) and verify the writer reports which records
were genuinely new so BaseScraper can broadcast only fresh arrests.
"""
from unittest.mock import MagicMock, patch

from core.models import ArrestRecord


def _make_writer(bulk_result=None):
    """Build a MongoWriter with a fully mocked MongoClient."""
    with patch("writers.mongo_writer.MongoClient") as mock_client_cls, \
         patch("writers.mongo_writer.settings") as mock_settings:
        mock_settings.MONGODB_URI = "mongodb://mock"
        mock_settings.MONGODB_DB_NAME = "test"
        mock_db = MagicMock()
        mock_client_cls.return_value.__getitem__.return_value = mock_db

        from writers.mongo_writer import MongoWriter
        writer = MongoWriter(uri="mongodb://mock", db_name="test")

        if bulk_result is not None:
            writer.arrests.bulk_write = MagicMock(return_value=bulk_result)
        return writer


def _bulk_result(upserted_indexes, modified=0):
    res = MagicMock()
    res.upserted_count = len(upserted_indexes)
    res.modified_count = modified
    res.bulk_api_result = {"upserted": [{"index": i, "_id": f"id{i}"} for i in upserted_indexes]}
    return res


def test_write_records_uses_state_aware_filter():
    writer = _make_writer(_bulk_result([0]))
    rec = ArrestRecord(County="Lee", State="GA", Booking_Number="B100", Full_Name="DOE, JOHN")

    writer.write_records([rec], "Lee")

    ops = writer.arrests.bulk_write.call_args[0][0]
    flt = ops[0]._filter
    assert flt == {"state": "GA", "county": "Lee", "booking_number": "B100"}, (
        "Dedup filter must include state so Lee (GA) never overwrites Lee (FL)"
    )


def test_write_records_defaults_state_to_fl():
    writer = _make_writer(_bulk_result([0]))
    rec = ArrestRecord(County="Lee", Booking_Number="B200", Full_Name="ROE, JANE")
    writer.write_records([rec], "Lee")
    flt = writer.arrests.bulk_write.call_args[0][0][0]._filter
    assert flt["state"] == "FL"


def test_write_records_returns_new_record_indexes():
    writer = _make_writer(_bulk_result([1], modified=1))
    recs = [
        ArrestRecord(County="Bacon", State="GA", Booking_Number="A1", Full_Name="OLD, GUY"),
        ArrestRecord(County="Bacon", State="GA", Booking_Number="A2", Full_Name="NEW, GUY"),
    ]
    stats = writer.write_records(recs, "Bacon")
    assert stats["new_records"] == 1
    assert stats["new_record_indexes"] == [1], (
        "Writer must report which input records were upserted (new) so the "
        "scraper broadcasts only genuinely new arrests"
    )


def test_ensure_indexes_creates_state_aware_unique_keys():
    writer = _make_writer()
    index_calls = [c.kwargs.get("name") or (c.args and c.args[0]) for c in writer.arrests.create_index.call_args_list]
    names = [c.kwargs.get("name") for c in writer.arrests.create_index.call_args_list]
    assert "dedup_state_county_booking" in names
    status_names = [c.kwargs.get("name") for c in writer.scraper_status.create_index.call_args_list]
    assert "idx_scraper_status_state_county" in status_names
