"""CoS rule (2026-10-09): a Miami-Dade internal booking key is never printed.

Miami-Dade docs keep ``md_dedupe_v2:<sha256>`` in ``booking_number`` as the
routing id (unique index + dashboard routes). Dashboard displays, documents
and CSV/XLSX exports print it blank (outbound alerts, the response middleware
and the global JS guard are in the stacked follow-up). Representative functional tests plus grep-style guards that
fail when a new sink bypasses the shared helpers.
"""
from __future__ import annotations

import ast
import csv
import io
import re
from pathlib import Path

import pytest

from core.booking_identity import (
    public_booking_number,
    redact_internal_keys,
    redact_workbook,
    redacting_csv_writer,
    redacting_dict_writer,
)

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
KEY = "md_dedupe_v2:" + "ab" * 32  # synthetic
PUBLIC = "2026-123456"


# ── helpers ────────────────────────────────────────────────────────────────

def test_public_booking_number_blank_for_key_only():
    assert public_booking_number(KEY) == ""
    assert public_booking_number(PUBLIC) == PUBLIC


def test_redact_removes_key_substrings_deep_and_keeps_text():
    payload = {"text": f"New bond {KEY} for Doe", "blocks": [{"t": KEY}], "n": 5, "tup": (KEY, PUBLIC)}
    out = redact_internal_keys(payload)
    assert KEY not in repr(out)
    assert out["text"] == "New bond  for Doe"
    assert out["blocks"][0]["t"] == ""
    assert out["n"] == 5 and out["tup"] == ("", PUBLIC)
    assert payload["text"].endswith("Doe") and KEY in payload["text"]  # input untouched


def test_csv_writers_redact_rows():
    buf = io.StringIO()
    w = redacting_csv_writer(buf)
    w.writerow(["booking_number", "name"])
    w.writerows([[KEY, "Doe"], [PUBLIC, "Roe"]])
    d = redacting_dict_writer(buf, fieldnames=["booking_number"])
    d.writeheader()
    d.writerow({"booking_number": KEY})
    text = buf.getvalue()
    assert "md_dedupe_v" not in text and PUBLIC in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1] == ["", "Doe"]


def test_workbook_cells_redacted_before_save():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Booking", "Name"])
    ws.append([KEY, f"Doe ({KEY})"])
    ws.append([PUBLIC, "Roe"])
    redact_workbook(wb)
    vals = [c.value for row in ws.iter_rows() for c in row]
    assert not any(isinstance(v, str) and "md_dedupe_v" in v for v in vals)
    assert PUBLIC in vals and "Doe ()" in vals


def test_checkin_evidence_filename_never_prints_key():
    from dashboard.services.checkin_evidence_service import safe_booking_filename

    assert "md_dedupe" not in safe_booking_filename(KEY)
    assert safe_booking_filename(KEY) == "booking"
    assert safe_booking_filename(PUBLIC) == PUBLIC


# ── grep-style guards ──────────────────────────────────────────────────────

_PY_DIRS = ("dashboard", "core", "writers", "services")
def _py_files():
    for d in _PY_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" not in p.parts:
                yield p


def _call_name(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_guard_no_raw_csv_writers():
    pat = re.compile(r"\bcsv\.(writer|DictWriter)\(")
    offenders = [str(p.relative_to(ROOT)) for p in _py_files()
                 if p.name != "booking_identity.py" and pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not offenders, "use redacting_csv_writer / redacting_dict_writer: " + ", ".join(offenders)


def test_guard_every_workbook_save_is_redacted():
    offenders = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8", errors="ignore")
        if "openpyxl" not in src:
            continue
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.get_source_segment(src, fn) or ""
            if re.search(r"\bwb\.save\(", body_src) and "redact_workbook(" not in body_src:
                offenders.append(f"{p.relative_to(ROOT)}:{fn.name}")
    assert not offenders, "XLSX exports must call redact_workbook before save: " + ", ".join(offenders)


@pytest.mark.parametrize("name, min_count", [
    ("sl-tasks.js", 1), ("sl-relationships.js", 2), ("sl-indemnitor.js", 2),
    ("sl-active-bonds.js", 4), ("sl-active-bonds-ext.js", 2),
])
def test_guard_list_views_use_booking_label(name, min_count):
    assert (DASH / name).read_text(encoding="utf-8").count("slBookingLabel") >= min_count


def test_guard_js_downloads_redacted():
    offenders = []
    for js in DASH.glob("*.js"):
        src = js.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"new Blob\(\[([^\]]*)\]", src):
            inner = m.group(1)
            if "slRedactKeys" in inner or inner.strip() in ("svg",):
                continue
            offenders.append(f"{js.name}: {inner.strip()[:40]}")
    assert not offenders, "JS text downloads must pass window.slRedactKeys: " + ", ".join(offenders)


def test_js_files_parse():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    for name in ("sl-core.js", "sl-tasks.js", "sl-relationships.js",
                 "sl-indemnitor.js", "sl-active-bonds.js", "sl-active-bonds-ext.js"):
        r = subprocess.run([node, "--check", str(DASH / name)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr[:300]}"


def test_sl_core_defines_label_and_redact_helpers():
    """List views and JS downloads use sl-core's helpers directly (no global guard needed)."""
    js = (DASH / "sl-core.js").read_text(encoding="utf-8")
    assert "window.slBookingLabel =" in js and "window.slRedactKeys =" in js
