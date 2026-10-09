#!/usr/bin/env python3
"""Rewrite the Write Bond golden JSON files. Opt-in only.

CI must not set ``WRITE_BOND_REGEN_GOLDEN``. This script exits without
writing when that variable is anything other than ``1``.

    WRITE_BOND_REGEN_GOLDEN=1 python scripts/regen_write_bond_goldens.py

The files are ``tests/golden/write_bond_osi.json``,
``tests/golden/write_bond_palmetto.json``, and the per-template pair
``write_bond_osi_template.json`` / ``write_bond_palmetto_template.json``.
Review the diff before committing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if os.environ.get("WRITE_BOND_REGEN_GOLDEN") != "1":
        print(
            "Refusing to rewrite goldens. Set WRITE_BOND_REGEN_GOLDEN=1 to opt in.",
            file=sys.stderr,
        )
        return 2
    os.chdir(ROOT)
    import pytest

    return pytest.main([
        "-q",
        "tests/test_write_bond_golden_smoke.py",
        "tests/test_write_bond_template_goldens.py",
        "-k",
        "test_write_bond_golden_fields or test_write_bond_template_fields",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
