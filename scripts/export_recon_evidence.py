#!/usr/bin/env python3
"""Export non-PII county source-contract evidence from audited state reports.

Input reports contain only source-contract facts. The output stores the exact
county/FIPS key, public source URL if verified, access posture, recommendation,
and evidence note needed to regenerate the canonical matrix. It deliberately
excludes person-level booking data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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
            if len(cells) == len(header):
                row = dict(zip(header, cells))
                row["FIPS"] = row["FIPS"].strip()[-3:]
                row["Recommended Shamrock state"] = row["Recommended Shamrock state"].strip().strip("`").casefold()
                rows.append(row)
        return rows
    raise RuntimeError("county findings table not found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = []
    for report in sorted(args.reports_dir.glob("*_recon_report.md")):
        state = report.name.split("_", 1)[0]
        for row in _table_rows(report.read_text()):
            records.append({
                "state": state,
                "county_fips": row["FIPS"],
                "passive_recommendation": row["Recommended Shamrock state"],
                "official_source_url": row["Official source URL"],
                "access_posture": row["Access posture"],
                "evidence_note": row["Evidence note"],
            })
    records.sort(key=lambda row: (row["state"], row["county_fips"]))
    keys = {(row["state"], row["county_fips"]) for row in records}
    if len(keys) != len(records):
        raise RuntimeError("duplicate state/FIPS evidence rows")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"records": records}, indent=2) + "\n")
    print(json.dumps({"records": len(records)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
