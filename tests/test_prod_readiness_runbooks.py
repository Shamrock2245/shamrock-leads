"""Contract tests for production-readiness runbooks (B1 Lee failure, B4 public endpoints, B5/B6 Mongo & proxy)."""

from pathlib import Path

from dashboard.auth.pin_middleware import OPEN_PATHS, OPEN_PREFIXES

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"


def test_lee_county_failure_runbook_covers_freshness_findings():
    path = RUNBOOKS_DIR / "lee-county-scraper-failure.md"
    assert path.is_file(), "Missing docs/runbooks/lee-county-scraper-failure.md"
    text = path.read_text(encoding="utf-8")
    for code in (
        "no_status",
        "stale",
        "records_write_failed",
        "repeated_errors",
        "zero_rows_site_has_bookings",
        "window_gap",
        "max_instances",
    ):
        assert code in text, f"Lee runbook missing finding code: {code}"


def test_mongo_backup_restore_and_proxy_runbook_covers_proxy_and_indexes():
    path = RUNBOOKS_DIR / "mongo-backup-restore-and-proxy.md"
    assert path.is_file(), "Missing docs/runbooks/mongo-backup-restore-and-proxy.md"
    text = path.read_text(encoding="utf-8")
    for token in (
        "mongodump",
        "mongorestore",
        "MONGO_PROXY_V2_URL",
        "audit_events",
        "booking_number",
        "ingestion_log",
    ):
        assert token in text, f"Mongo/proxy runbook missing: {token}"


def test_public_endpoints_manifest_documents_all_open_paths_and_prefixes():
    path = RUNBOOKS_DIR / "public-endpoints-security-manifest.md"
    assert path.is_file(), "Missing docs/runbooks/public-endpoints-security-manifest.md"
    text = path.read_text(encoding="utf-8")
    for open_path in OPEN_PATHS:
        assert open_path in text, f"OPEN_PATHS entry {open_path} not documented in public-endpoints-security-manifest.md"
    for prefix in OPEN_PREFIXES:
        base = prefix.rstrip("/")
        assert base in text, f"OPEN_PREFIXES entry {prefix} not documented in public-endpoints-security-manifest.md"
