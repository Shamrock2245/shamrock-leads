#!/usr/bin/env python3
"""Build the canonical county source-contract reconnaissance matrix.

The matrix merges the official Census county-equivalent worklist with audited,
passive state reports. It documents evidence posture only; it does not modify
runtime scraper registration or source-contract states.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "dashboard" / "extensions.py"

REQUIRED_COLUMNS = [
    "County",
    "FIPS",
    "Repo coverage",
    "Official source URL",
    "Platform/interface",
    "Broad public roster",
    "Source-issued identifier on listing",
    "Booking date/time on listing",
    "Access posture",
    "Recommended Shamrock state",
    "Evidence note",
]


def _table_rows(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    for index, raw_header in enumerate(lines):
        if not raw_header.strip().startswith("|"):
            continue
        header = [cell.strip() for cell in raw_header.strip().strip("|").split("|")]
        if header != REQUIRED_COLUMNS:
            continue
        rows = []
        for raw_row in lines[index + 2:]:
            if not raw_row.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in raw_row.strip().strip("|").split("|")]
            if len(cells) != len(header):
                continue
            row = dict(zip(header, cells))
            row["FIPS"] = row["FIPS"].strip()[-3:]
            row["Recommended Shamrock state"] = row["Recommended Shamrock state"].strip().strip("`").casefold()
            rows.append(row)
        return rows
    raise RuntimeError("county findings table not found")


def _runtime_source_states() -> dict[str, str]:
    tree = ast.parse(EXTENSIONS.read_text())
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "SCRAPER_SOURCE_STATES":
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, dict):
            raise RuntimeError("SCRAPER_SOURCE_STATES must be a dictionary")
        return {str(label): str(state) for label, state in value.items()}
    raise RuntimeError("SCRAPER_SOURCE_STATES assignment not found")


def _matrix_status(passive_status: str, runtime_status: str) -> str:
    """Keep deployed source truth authoritative over passive reconnaissance."""
    if runtime_status in {"verified_public", "fail_closed"}:
        return runtime_status
    if passive_status == "productive":
        return "candidate_productive"
    if passive_status == "recon_only":
        return "recon_only"
    return "unverified"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("evidence_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.inventory.read_text())
    records = payload["records"]
    runtime_states = _runtime_source_states()
    by_state_fips = {(row["state"], row["county_fips"]): row for row in records}
    evidence_payload = json.loads(args.evidence_file.read_text())
    evidence = {}
    for raw in evidence_payload["records"]:
        key = (str(raw["state"]), str(raw["county_fips"]))
        if key not in by_state_fips:
            raise RuntimeError(f"evidence has unknown state/FIPS row: {key}")
        if key in evidence:
            raise RuntimeError(f"duplicate evidence row: {key}")
        evidence[key] = {
            "Recommended Shamrock state": str(raw["passive_recommendation"]),
            "Official source URL": str(raw["official_source_url"]),
            "Access posture": str(raw["access_posture"]),
            "Evidence note": str(raw["evidence_note"]),
        }
    for key, record in by_state_fips.items():
        if key in evidence:
            continue
        if record.get("scope_type") != "special_registry_scope":
            raise RuntimeError(f"missing report row: {key}")
        evidence[key] = {
            "Recommended Shamrock state": "not_verified",
            "Official source URL": "—",
            "Access posture": "Registered non-county scope; no county-equivalent source contract asserted",
            "Evidence note": "This dashboard registration is outside the Census county-equivalent inventory. It remains unverified until a scope-specific public source-contract validation is documented.",
        }
    unexpected = sorted(set(evidence) - set(by_state_fips))
    if unexpected:
        raise RuntimeError(f"report has unknown rows: {unexpected[:10]}")

    for record in records:
        label = f"{record['county']} ({record['state']})"
        passive_status = evidence[(record["state"], record["county_fips"])]["Recommended Shamrock state"]
        record["matrix_status"] = _matrix_status(passive_status, runtime_states.get(label, "unverified"))
    counts = Counter((row["state"], row["matrix_status"]) for row in records)
    registered = Counter(row["state"] for row in records if row["registry_status"] == "registered")
    total_status = Counter(row["matrix_status"] for row in records)

    lines = [
        "# County Source-Contract Reconnaissance Matrix",
        "",
        "> **Scope:** All 942 Census county-equivalents in the Shamrock multi-state worklist plus five registered non-county runtime scopes. **Method:** passive, ordinary public-access source-contract review only. No person-level arrest records, images, profile pages, sequential identifiers, login, CAPTCHA bypass, or source-control workaround were used.",
        ">",
        "> **Interpretation:** `registered` is a code/scheduler-coverage fact; it is not evidence that a county source is valid or producing records. Only `verified_public` and `fail_closed` are copied from the explicit deployed `SCRAPER_SOURCE_STATES` registry. `candidate_productive` reflects a bounded passive listing observation and does **not** authorize a parser, alter a source state, or establish Mongo/alert telemetry. `recon_only` and `unverified` require county-specific validation before any record-emitting change.",
        "",
        "## Jurisdiction scope",
        "",
        "| Group | States | County-equivalents | Basis |",
        "|---|---|---:|---|",
        "| OSI and Palmetto licensed writing county-equivalent scope | FL, SC, NC, TN, TX, CT, LA, MS | 716 | `docs/policies/surety-policy.md` + 2020 Census Gazetteer |",
        "| Adjacent repository county-equivalent coverage | GA, AL | 226 | Existing repository and roadmap coverage; not treated as a Palmetto license assertion |",
        "| Registered non-county scopes | CT and TN | 5 | Canonical `REGISTERED_COUNTIES` labels outside Census county equivalents |",
        "| **Total worklist** | **10 states** | **947** | 942 Census county equivalents + 5 registered non-county scopes |",
        "",
        "## Summary by state",
        "",
        "| State | Worklist | Registered in repo | Verified public | Candidate productive | Recon only | Unverified | Fail closed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for state in sorted({row["state"] for row in records}):
        state_records = [row for row in records if row["state"] == state]
        lines.append(
            f"| {state} | {len(state_records)} | {registered[state]} | {counts[(state, 'verified_public')]} | {counts[(state, 'candidate_productive')]} | {counts[(state, 'recon_only')]} | {counts[(state, 'unverified')]} | {counts[(state, 'fail_closed')]} |"
        )
    lines.extend([
        "",
        f"**Aggregate matrix counts:** verified public {total_status['verified_public']}; candidate productive {total_status['candidate_productive']}; recon only {total_status['recon_only']}; unverified {total_status['unverified']}; fail closed {total_status['fail_closed']}.",
        "",
        "## County matrix",
        "",
        "| State | FIPS or scope key | County-equivalent or special scope | Surety/repository scope | Repo coverage | Matrix source state | Official source or landing URL | Access posture | Evidence note |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for record in sorted(records, key=lambda row: (row["state"], row["county"].casefold(), row["county_fips"])):
        row = evidence[(record["state"], record["county_fips"])]
        source = row["Official source URL"]
        source = source if re.match(r"https?://", source) else "—"
        lines.append(
            "| {state} | {fips} | {county} | {scope} | {coverage} | {recommendation} | {source} | {access} | {note} |".format(
                state=record["state"],
                fips=record["county_fips"],
                county=_escape(record["census_name"]),
                scope=_escape(record["jurisdiction"]),
                coverage=_escape(record["registry_status"]),
                recommendation=_escape(record["matrix_status"]),
                source=_escape(source),
                access=_escape(row["Access posture"]),
                note=_escape(row["Evidence note"]),
            )
        )
    lines.extend([
        "",
        "## Operating rule",
        "",
        "A row may be promoted from `recon_only`, `unverified`, or `candidate_productive` only after a county-specific source validation records the official listing URL, complete public name, source-issued booking/inmate identifier, booking or arrest date/time, permitted pagination, and no access-control workaround. Rows in `fail_closed` must remain non-emitting. Any parser change must preserve `State + County + Booking_Number` uniqueness and update the relevant state registry, `SCRAPER_SOURCE_STATES` when appropriate, tests, `STATUS.md`, and this matrix.",
        "",
        "## Sources",
        "",
        "1. [2020 Census Gazetteer county file](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_counties_national.zip) — county-equivalent names and FIPS worklist.",
        "2. `docs/policies/surety-policy.md` — active OSI and Palmetto licensed-writing scope.",
        "3. `docs/recon/county_source_contract_evidence.json` — versioned, non-PII passive evidence for all 942 Census county-equivalent rows.",
        "4. `dashboard/extensions.py` — canonical registered scraper labels; state registry documents remain the source of county-specific source decisions.",
        "5. `docs/recon/LOUISIANA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` — bounded public-contract validation for Louisiana candidate and guarded rows.",
        "6. `docs/recon/TENNESSEE_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` — bounded metadata-only validation for the nine Tennessee guarded rows.",
        "7. `docs/recon/NORTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` — bounded metadata-only validation for the ten North Carolina guarded rows.",
        "8. `docs/recon/SOUTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md` — bounded metadata-only validation for the fourteen South Carolina guarded rows.",
        "9. `docs/recon/CONNECTICUT_JUDICIAL_DOCKET_VALIDATION_2026-08-15.md` — court-docket versus arrest-source validation for the Connecticut docket fleet.",
        "",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines))
    print(json.dumps({"rows": len(records), "recommendations": dict(total_status)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
