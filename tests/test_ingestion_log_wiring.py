"""Contract tests for wiring BaseScraper.run() into MongoWriter.log_ingestion (ingestion_log)."""

from typing import List
from unittest.mock import MagicMock, patch

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import ParseDriftError
from writers.mongo_writer import MongoWriter


class _FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, filt, update, upsert=False):
        return MagicMock(matched_count=1)

    def find_one(self, filt=None, proj=None):
        return {}


def _make_mongo_writer_with_fakes() -> MongoWriter:
    writer = MongoWriter.__new__(MongoWriter)
    writer.ingestion_log = _FakeCollection()
    writer.scraper_status = _FakeCollection()
    writer.write_records = MagicMock(
        return_value={
            "total_records": 1,
            "new_records": 1,
            "updated_records": 0,
            "duplicates_skipped": 0,
            "qualified_records": 1,
            "new_record_indexes": [],
        }
    )
    return writer


class _SuccessScraper(BaseScraper):
    county = "Lee"
    state = "FL"
    scraper_id = "scraper_lee"
    SOURCE_CONTRACT_VALIDATED = True

    def scrape(self) -> List[ArrestRecord]:
        return [
            ArrestRecord(
                County="Lee",
                State="FL",
                Booking_Number="2026-000100",
                Full_Name="Sample, Test",
                Charges="TRESPASS",
                Bond_Amount="1000",
                Status="IN CUSTODY",
            )
        ]


class _EmptyScraper(BaseScraper):
    county = "Hendry"
    state = "FL"
    scraper_id = "scraper_hendry"
    SOURCE_CONTRACT_VALIDATED = True

    def scrape(self) -> List[ArrestRecord]:
        return []


class _FailingScraper(BaseScraper):
    county = "Collier"
    state = "FL"
    scraper_id = "scraper_collier"
    SOURCE_CONTRACT_VALIDATED = True

    def scrape(self) -> List[ArrestRecord]:
        raise ParseDriftError("table header missing Booking_Number")


class _FailClosedScraper(BaseScraper):
    county = "Sarasota"
    state = "FL"
    scraper_id = "scraper_sarasota"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = "Interactive CAPTCHA gate"

    def scrape(self) -> List[ArrestRecord]:
        raise AssertionError("fail_closed scraper must not run scrape()")


def test_mongo_writer_log_ingestion_records_source_count_error_class_duration():
    writer = _make_mongo_writer_with_fakes()
    writer.log_ingestion(
        "Lee",
        {"new_records": 3, "updated_records": 1},
        error=None,
        state="FL",
        source="scraper_lee",
        count=4,
        error_class=None,
        duration=2.34,
        status="ok",
        scraper_id="scraper_lee",
    )
    assert len(writer.ingestion_log.docs) == 1
    doc = writer.ingestion_log.docs[0]
    assert doc["source"] == "scraper_lee"
    assert doc["county"] == "Lee"
    assert doc["county_label"] == "Lee (FL)"
    assert doc["state"] == "FL"
    assert doc["count"] == 4
    assert doc["total_records"] == 4
    assert doc["new_records"] == 3
    assert doc["updated_records"] == 1
    assert doc["duration"] == 2.3
    assert doc["duration_seconds"] == 2.3
    assert doc["status"] == "ok"
    assert doc["error_class"] is None
    assert doc["error"] is None


def test_base_scraper_run_logs_successful_and_empty_runs_to_ingestion_log():
    writer = _make_mongo_writer_with_fakes()
    with patch("scrapers.base_scraper._slack") as slack_mock, \
         patch.object(BaseScraper, "_check_disk_space", return_value={"ok": True}):
        ok_scraper = _SuccessScraper()
        ok_scraper.run(writers=[writer])

        empty_scraper = _EmptyScraper()
        empty_scraper.run(writers=[writer])
        assert slack_mock is not None

    assert len(writer.ingestion_log.docs) == 2
    first, second = writer.ingestion_log.docs

    assert first["source"] == "scraper_lee"
    assert first["county"] == "Lee"
    assert first["state"] == "FL"
    assert first["count"] == 1
    assert first["error_class"] is None
    assert isinstance(first["duration"], float) and first["duration"] >= 0.0
    assert first["status"] == "ok"

    assert second["source"] == "scraper_hendry"
    assert second["county"] == "Hendry"
    assert second["count"] == 0
    assert second["error_class"] is None
    assert isinstance(second["duration"], float) and second["duration"] >= 0.0
    assert second["status"] == "empty"


def test_base_scraper_run_logs_classified_failure_to_ingestion_log():
    writer = _make_mongo_writer_with_fakes()
    with patch("scrapers.base_scraper._slack"), \
         patch("scrapers.base_scraper._error_tracker", None), \
         patch.object(BaseScraper, "_check_disk_space", return_value={"ok": True}):
        failing = _FailingScraper()
        res = failing.run(writers=[writer])

    assert res["status"] == "error"
    assert res["error_class"] == "parse_drift"
    assert len(writer.ingestion_log.docs) == 1
    doc = writer.ingestion_log.docs[0]
    assert doc["source"] == "scraper_collier"
    assert doc["county"] == "Collier"
    assert doc["state"] == "FL"
    assert doc["count"] == 0
    assert doc["error_class"] == "parse_drift"
    assert isinstance(doc["duration"], float) and doc["duration"] >= 0.0
    assert doc["status"] == "error"
    assert "Booking_Number" in (doc["error"] or "")


def test_fail_closed_scraper_does_not_touch_ingestion_log():
    writer = _make_mongo_writer_with_fakes()
    guarded = _FailClosedScraper()
    res = guarded.run(writers=[writer])
    assert res["source_contract_state"] == "fail_closed"
    assert writer.ingestion_log.docs == []


def test_write_failure_zeros_success_counters_in_ingestion_log():
    writer = _make_mongo_writer_with_fakes()
    writer.write_records = MagicMock(side_effect=RuntimeError("Mongo timeout"))
    with patch("scrapers.base_scraper._slack"), \
         patch.object(BaseScraper, "_check_disk_space", return_value={"ok": True}):
        scraper = _SuccessScraper()
        scraper.run(writers=[writer])

    assert len(writer.ingestion_log.docs) == 1
    doc = writer.ingestion_log.docs[0]
    assert doc["status"] == "error"
    assert doc["error_class"] == "write_failed"
    assert doc["error"] == "records_write_failed"
    assert doc["count"] == 0
    assert doc["total_records"] == 0
    assert doc["new_records"] == 0
    assert doc["updated_records"] == 0


def test_disk_full_guard_logs_error_run_to_ingestion_log():
    writer = _make_mongo_writer_with_fakes()
    with patch("scrapers.base_scraper._slack"), \
         patch.object(
             BaseScraper,
             "_check_disk_space",
             return_value={"ok": False, "percent_used": 98.5, "free_gb": 0.4},
         ):
        scraper = _SuccessScraper()
        res = scraper.run(writers=[writer])

    assert "Disk full" in res["error"]
    assert len(writer.ingestion_log.docs) == 1
    doc = writer.ingestion_log.docs[0]
    assert doc["source"] == "scraper_lee"
    assert doc["status"] == "error"
    assert doc["error_class"] == "disk_full"
    assert doc["count"] == 0
    assert doc["new_records"] == 0
    assert "Disk full" in (doc["error"] or "")

