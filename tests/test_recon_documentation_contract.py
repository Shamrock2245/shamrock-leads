from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "dashboard" / "extensions.py"
README = ROOT / "README.md"
AGENTS = ROOT / "AGENTS.md"
ROADMAP = ROOT / "docs" / "MULTI_STATE_SCRAPER_ROADMAP.md"
INVENTORY = ROOT / "docs" / "recon" / "county_recon_inventory.json"
MATRIX = ROOT / "docs" / "recon" / "COUNTY_SOURCE_CONTRACT_MATRIX.md"


def _registered_counts() -> Counter[str]:
    tree = ast.parse(EXTENSIONS.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "REGISTERED_COUNTIES" for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "sorted":
            value = value.args[0]
        assert isinstance(value, ast.List)
        labels = [element.value for element in value.elts if isinstance(element, ast.Constant) and isinstance(element.value, str)]
        return Counter(re.search(r"\(([A-Z]{2})\)$", label).group(1) for label in labels)
    raise AssertionError("REGISTERED_COUNTIES assignment not found")


def test_recon_inventory_is_complete_for_the_documented_ten_state_scope():
    payload = json.loads(INVENTORY.read_text())
    records = payload["records"]
    counts = Counter(row["state"] for row in records)

    assert len(records) == 947
    assert counts == {
        "AL": 67,
        "CT": 12,
        "FL": 67,
        "GA": 159,
        "LA": 64,
        "MS": 82,
        "NC": 100,
        "SC": 46,
        "TN": 96,
        "TX": 254,
    }
    assert sum(row["scope_type"] == "county_equivalent" for row in records) == 942
    assert sum(row["scope_type"] == "special_registry_scope" for row in records) == 5
    assert len({(row["state"], row["county_fips"]) for row in records}) == 947


def test_recon_matrix_has_one_county_row_per_inventory_record():
    inventory = json.loads(INVENTORY.read_text())["records"]
    expected = {(row["state"], row["county_fips"]) for row in inventory}
    actual = set()
    for line in MATRIX.read_text().splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 9 or not re.fullmatch(r"[A-Z]{2}", cells[0]) or not re.fullmatch(r"(?:\d{3}|special_[a-z0-9_]+)", cells[1]):
            continue
        actual.add((cells[0], cells[1]))

    assert actual == expected


def test_retired_louisiana_endpoints_are_reported_as_fail_closed():
    matrix = MATRIX.read_text()
    for row in (
        "| LA | 019 | Calcasieu Parish | Palmetto | registered | fail_closed |",
        "| LA | 071 | Orleans Parish | Palmetto | registered | fail_closed |",
        "| LA | 103 | St. Tammany Parish | Palmetto | registered | fail_closed |",
    ):
        assert row in matrix


def test_unvalidated_tennessee_batch_is_reported_as_fail_closed():
    matrix = MATRIX.read_text()
    for fips, county in (
        ("037", "Davidson"),
        ("065", "Hamilton"),
        ("093", "Knox"),
        ("125", "Montgomery"),
        ("149", "Rutherford"),
        ("157", "Shelby"),
        ("165", "Sumner"),
        ("187", "Williamson"),
        ("189", "Wilson"),
    ):
        assert f"| TN | {fips} | {county} County | Palmetto | registered | fail_closed |" in matrix


def test_unvalidated_north_carolina_batch_is_reported_as_fail_closed():
    matrix = MATRIX.read_text()
    for fips, county in (
        ("027", "Caldwell"),
        ("037", "Chatham"),
        ("051", "Cumberland"),
        ("057", "Davidson"),
        ("081", "Guilford"),
        ("083", "Halifax"),
        ("151", "Randolph"),
        ("165", "Scotland"),
        ("179", "Union"),
        ("183", "Wake"),
    ):
        assert f"| NC | {fips} | {county} County | Palmetto | registered | fail_closed |" in matrix


def test_unvalidated_south_carolina_batch_is_reported_as_fail_closed():
    matrix = MATRIX.read_text()
    for fips, county in (
        ("007", "Anderson"),
        ("009", "Bamberg"),
        ("013", "Beaufort"),
        ("015", "Berkeley"),
        ("045", "Greenville"),
        ("051", "Horry"),
        ("053", "Jasper"),
        ("055", "Kershaw"),
        ("059", "Laurens"),
        ("061", "Lee"),
        ("067", "Marion"),
        ("081", "Saluda"),
        ("087", "Union"),
        ("091", "York"),
    ):
        assert f"| SC | {fips} | {county} County | Palmetto | registered | fail_closed |" in matrix


def test_connecticut_court_docket_counties_are_reported_as_fail_closed():
    matrix = MATRIX.read_text()
    assert "| CT | 003 | Hartford County | Palmetto | registered | fail_closed |" in matrix
    assert "| CT | 009 | New Haven County | Palmetto | registered | fail_closed |" in matrix


def test_active_docs_match_the_canonical_358_scraper_registry():
    counts = _registered_counts()
    assert sum(counts.values()) == 358
    expected = {"AL": 16, "CT": 6, "FL": 67, "GA": 85, "LA": 13, "MS": 9, "NC": 60, "SC": 46, "TN": 22, "TX": 34}
    assert counts == expected

    readme = README.read_text()
    assert "358 registered county scrapers" in readme
    assert "| **Total** | **358**" in readme
    for state, count in expected.items():
        assert f"{state} {count}" in readme

    assert "947-scope reconnaissance matrix" in readme
    assert "947 rows total" in AGENTS.read_text()
    assert "= **358**" in ROADMAP.read_text()
    roadmap = ROADMAP.read_text()
    assert "947 rows total" in roadmap
    assert "20 county paths are explicitly `fail_closed`" in roadmap
    assert "TnCIS scope remains `unverified`" in roadmap
    assert "13 LA" in roadmap
