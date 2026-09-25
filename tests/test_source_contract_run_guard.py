from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class _UnvalidatedScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = "Fixture source contract is not validated."

    @property
    def county(self) -> str:
        return "Guarded"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> list[ArrestRecord]:
        raise AssertionError("run must stop before scrape()")


def test_unvalidated_source_contract_stops_before_scrape_or_writer():
    scraper = _UnvalidatedScraper()
    writer = Mock()

    result = scraper.run(writers=[writer])

    assert result == {
        "county": "Guarded",
        "records_scraped": 0,
        "elapsed_seconds": 0,
        "source_contract_state": "fail_closed",
        "error": "Fixture source contract is not validated.",
    }
    writer.write_records.assert_not_called()


# ── Self-heal / matrix-drift suites (CI bridge) ─────────────────────────────
# ci.yml lists test modules explicitly and editing the workflow needs a token
# with `workflow` scope. Until ci.yml lists these modules directly, this test
# runs them in an isolated subprocess, so the "Syntax + contract suite" job
# fails when retry/backoff, auto-disable, error classification, or the
# registry/matrix drift gate regress. It runs only the listed modules that the
# current session did not collect (so modules ci.yml does not list, such as
# tests/test_fl_gap_queue.py and tests/test_gap_queue_nc_tx_sc.py, still run in CI) and skips itself when all of
# them are already collected (e.g. `pytest tests/`).
_SELF_HEAL_SUITES = (
    "tests/test_scraper_resilience.py",
    "tests/test_base_scraper_self_heal.py",
    "tests/test_source_state_drift.py",
    "tests/test_dashboard_source_states.py",
    "tests/test_lee_rate_limit.py",
    "tests/test_tncis_fail_closed.py",
    "tests/test_fl_gap_queue.py",
    "tests/test_gap_queue_nc_tx_sc.py",
)


def test_self_heal_and_matrix_drift_suites_pass(request):
    root = Path(__file__).resolve().parents[1]
    collected = {Path(str(item.fspath)).resolve() for item in request.session.items}
    pending = [s for s in _SELF_HEAL_SUITES if (root / s).resolve() not in collected]
    if not pending:
        pytest.skip("self-heal suites already collected in this session")
    env = dict(os.environ)
    env.setdefault("ENV", "test")
    env.setdefault("SECRET_KEY", "ci-not-a-real-secret")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", *pending],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, (proc.stdout[-6000:] + proc.stderr[-2000:])
    # Make the bridged run visible in the CI log (pytest -q hides passing output).
    summary = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"\n[ci-bridge] ran {', '.join(pending)}: {summary}")
