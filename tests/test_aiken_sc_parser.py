"""Aiken SC DTNSearch list parser — source Inmate ID# / qSO_NO only."""
from pathlib import Path

from scrapers.counties_sc.aiken import AikenScraper

FIXTURE = Path(__file__).parent / "fixtures" / "aiken_results_sample.html"


def test_aiken_parse_results_uses_qso_no():
    html = FIXTURE.read_text()
    rows = AikenScraper()._parse_results(html)
    assert len(rows) == 2
    assert {r["inmate_id"] for r in rows} == {"1008603", "1000399"}
    assert all(r["inmate_id"].isdigit() for r in rows)
    assert all(not r["inmate_id"].upper().startswith("AIK_") for r in rows)
    assert rows[0]["last"]
    assert rows[0]["arrest"]
