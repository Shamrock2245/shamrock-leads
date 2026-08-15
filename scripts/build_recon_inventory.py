#!/usr/bin/env python3
"""Build an evidence-bound county-equivalent reconnaissance inventory.

The script retrieves names and FIPS codes from the public 2020 Census county
Gazetteer, then compares them with the statically defined Shamrock REGISTERED_COUNTIES
list. It does not access jail, court, or booking sources and never processes
person-level data.
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_PATH = ROOT / "dashboard" / "extensions.py"
CENSUS_GAZETTEER = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/"
    "2020_Gaz_counties_national.zip"
)

# The active surety policy is authoritative for licensed writing jurisdictions.
SURETY_STATES = {
    "FL": "OSI + Palmetto",
    "SC": "Palmetto",
    "NC": "Palmetto",
    "TN": "Palmetto",
    "TX": "Palmetto",
    "CT": "Palmetto",
    "LA": "Palmetto",
    "MS": "Palmetto",
}
# These adjacent-market paths are in the repository and must be retained in the
# documentation/recon inventory, but they are not represented as current
# Palmetto licensed-writing states in the surety policy.
ADJACENT_REPO_STATES = {"GA": "Adjacent repo coverage", "AL": "Adjacent repo coverage"}
STATE_FIPS = {
    "AL": "01", "CT": "09", "FL": "12", "GA": "13", "LA": "22",
    "MS": "28", "NC": "37", "SC": "45", "TN": "47", "TX": "48",
}


def _parse_registered_counties() -> set[tuple[str, str]]:
    tree = ast.parse(EXTENSIONS_PATH.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "REGISTERED_COUNTIES" for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "sorted":
            value = value.args[0]
        if not isinstance(value, ast.List):
            raise RuntimeError("REGISTERED_COUNTIES must be a list or sorted(list)")
        labels = [element.value for element in value.elts if isinstance(element, ast.Constant) and isinstance(element.value, str)]
        parsed: set[tuple[str, str]] = set()
        for label in labels:
            match = re.fullmatch(r"(.+) \(([A-Z]{2})\)", label)
            if not match:
                raise RuntimeError(f"malformed registered county label: {label!r}")
            parsed.add((match.group(1), match.group(2)))
        return parsed
    raise RuntimeError("REGISTERED_COUNTIES assignment not found")


def _load_gazetteer_rows() -> list[dict[str, str]]:
    request = Request(CENSUS_GAZETTEER, headers={"User-Agent": "ShamrockLeadsReconInventory/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.endswith("_national.txt")]
        if len(names) != 1:
            raise RuntimeError("Census Gazetteer national county table not found")
        table = archive.read(names[0]).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(table), delimiter="\t"))


def _census_counties(gazetteer_rows: list[dict[str, str]], state_code: str) -> list[dict[str, str]]:
    results = []
    for raw in gazetteer_rows:
        geoid = str(raw.get("GEOID") or "")
        if len(geoid) != 5 or geoid[:2] != STATE_FIPS[state_code]:
            continue
        raw_name = str(raw.get("NAME") or "").strip()
        county_name = re.sub(r"\s+(County|Parish)$", "", raw_name, flags=re.IGNORECASE)
        results.append({
            "state": state_code,
            "state_fips": geoid[:2],
            "county_fips": geoid[2:],
            "census_name": raw_name,
            "county": county_name,
            "scope_type": "county_equivalent",
        })
    return sorted(results, key=lambda row: (row["county"].casefold(), row["county_fips"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Destination JSON path")
    parser.add_argument("--states", nargs="+", choices=sorted(STATE_FIPS), default=sorted(STATE_FIPS), help="State codes to include")
    args = parser.parse_args()

    registered = _parse_registered_counties()
    inventory = []
    gazetteer_rows = _load_gazetteer_rows()
    for state in args.states:
        jurisdiction = SURETY_STATES.get(state) or ADJACENT_REPO_STATES[state]
        for row in _census_counties(gazetteer_rows, state):
            registered_label = (row["county"], state)
            row["jurisdiction"] = jurisdiction
            row["registered_label"] = f"{row['county']} ({state})" if registered_label in registered else ""
            row["registry_status"] = "registered" if registered_label in registered else "recon_required"
            row["recon_status"] = "not_reviewed"
            inventory.append(row)

    # The canonical dashboard also contains state/city scopes that are not
    # Census county-equivalents (currently CT DOC, Statewide, Bridgeport, and
    # Stamford).  They must remain visible in the recon worklist rather than
    # disappearing from a county-only geography source.
    represented = {(row["county"], row["state"]) for row in inventory}
    for county, state in sorted(registered):
        if state not in args.states or (county, state) in represented:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", county.lower()).strip("_")
        inventory.append({
            "state": state,
            "state_fips": STATE_FIPS[state],
            "county_fips": f"special_{state.lower()}_{slug}",
            "census_name": f"{county} (non-county scope)",
            "county": county,
            "scope_type": "special_registry_scope",
            "jurisdiction": SURETY_STATES.get(state) or ADJACENT_REPO_STATES[state],
            "registered_label": f"{county} ({state})",
            "registry_status": "registered",
            "recon_status": "not_reviewed",
        })

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "2020 Census Gazetteer county-equivalent names and FIPS codes, plus canonical registered non-county scopes",
        "scope": {
            "licensed_surety_states": SURETY_STATES,
            "adjacent_repo_states": ADJACENT_REPO_STATES,
        },
        "records": inventory,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    counts = {}
    for row in inventory:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    print(json.dumps({"records": len(inventory), "worklist_scopes_by_state": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
