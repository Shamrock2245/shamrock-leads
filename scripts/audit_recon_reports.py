#!/usr/bin/env python3
"""Audit state reconnaissance reports against the county-equivalent worklist.

This validates coverage and report structure only. It never judges a roster
contract from a report alone; unsupported claims are flagged for manual review.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
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
ALLOWED_STATES = {"productive", "recon_only", "fail_closed", "not_verified"}


def _table_rows(text: str) -> tuple[list[str], list[list[str]]]:
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
                rows.append(cells)
        return header, rows
    return [], []


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())["records"]
    expected: dict[str, set[str]] = {}
    for row in inventory:
        expected.setdefault(row["state"], set()).add(str(row["county_fips"]))

    output: dict[str, object] = {"states": {}, "issues": []}
    all_issues: list[str] = []
    for state, expected_fips in sorted(expected.items()):
        path = args.reports_dir / f"{state}_recon_report.md"
        if not path.is_file():
            all_issues.append(f"{state}: missing report file")
            continue
        header, rows = _table_rows(path.read_text())
        state_issues = []
        if header != REQUIRED_COLUMNS:
            state_issues.append(f"unexpected table header: {header}")
        fips_index = header.index("FIPS") if "FIPS" in header else -1
        status_index = header.index("Recommended Shamrock state") if "Recommended Shamrock state" in header else -1
        url_index = header.index("Official source URL") if "Official source URL" in header else -1
        report_fips = {
            str(row[fips_index]).strip()[-3:]
            for row in rows
        } if fips_index >= 0 else set()
        missing_fips = sorted(expected_fips - report_fips)
        extra_fips = sorted(report_fips - expected_fips)
        if missing_fips:
            state_issues.append(f"missing FIPS rows: {', '.join(missing_fips)}")
        if extra_fips:
            state_issues.append(f"unexpected FIPS rows: {', '.join(extra_fips)}")
        statuses = Counter(
            row[status_index].strip().strip("`").casefold()
            for row in rows
        ) if status_index >= 0 else Counter()
        invalid_statuses = sorted(status for status in statuses if status not in ALLOWED_STATES)
        if invalid_statuses:
            state_issues.append(f"unsupported status values: {', '.join(invalid_statuses)}")
        productive_without_url = []
        if status_index >= 0 and url_index >= 0:
            productive_without_url = [
                row[0]
                for row in rows
                if row[status_index].strip().strip("`").casefold() == "productive"
                and not re.search(r"https?://", row[url_index])
            ]
        if productive_without_url:
            state_issues.append("productive rows without official URL: " + ", ".join(productive_without_url))
        output["states"][state] = {
            "expected_count": len(expected_fips),
            "report_row_count": len(rows),
            "missing_fips": missing_fips,
            "extra_fips": extra_fips,
            "recommended_states": dict(statuses),
            "issues": state_issues,
        }
        all_issues.extend(f"{state}: {issue}" for issue in state_issues)

    output["issues"] = all_issues
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"states": len(expected), "issues": len(all_issues)}, sort_keys=True))
    return 1 if all_issues else 0


if __name__ == "__main__":
    sys.exit(main())
