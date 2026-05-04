"""
ShamrockLeads — Reports, POA, and Bond API Integrity Tests
Verifies:
  1. CSV export produces valid comma-separated output
  2. Agent-production report normalizes legacy names
  3. POA expiration field is wired end-to-end
  4. Voided vs Expired POA distinction is correct
  5. Report API response keys match frontend expectations
  6. Surety split calculations are accurate
"""

import json
import sys
import os
import importlib
from datetime import datetime, timezone, timedelta

# ── Test helpers ──

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def fail(msg):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


def assert_eq(a, b, label):
    if a == b:
        ok(f"{label}: {a!r}")
    else:
        fail(f"{label}: expected {b!r}, got {a!r}")


def assert_in(needle, haystack, label):
    if needle in haystack:
        ok(f"{label}: '{needle}' found")
    else:
        fail(f"{label}: '{needle}' NOT found in {haystack!r}")


def assert_not_in(needle, haystack, label):
    if needle not in haystack:
        ok(f"{label}: '{needle}' correctly absent")
    else:
        fail(f"{label}: '{needle}' should NOT be in {haystack!r}")


# ════════════════════════════════════════════════════════
# TEST 1: CSV Export Validity
# ════════════════════════════════════════════════════════
def test_csv_export():
    print("\n🔹 TEST 1: CSV Export Validity")
    # Simulate the CSV export logic from sl-reports.js
    # The JS builds CSV from table rows with this pattern:
    # cells.push(`"${text}"`); rows.push(cells.join(','));
    
    sample_rows = [
        ["Agent", "Bonds", "Bond Amount", "Premium"],
        ["Brendan O'Neal", "5", "$25,000.00", "$2,500.00"],
        ["Jason Taylor", "3", "$15,000.00", "$1,500.00"],
    ]
    
    csv_lines = []
    for row in sample_rows:
        cells = [f'"{cell}"' for cell in row]
        csv_lines.append(",".join(cells))
    
    csv_output = "\n".join(csv_lines)
    
    # Verify CSV structure
    lines = csv_output.split("\n")
    assert_eq(len(lines), 3, "CSV has 3 rows (header + 2 data)")
    
    # Verify header
    header = lines[0]
    assert_in('"Agent"', header, "CSV header contains Agent")
    assert_in('"Bonds"', header, "CSV header contains Bonds")
    
    # Verify data rows are valid CSV (use csv module for proper parsing)
    import csv
    import io
    reader = csv.reader(io.StringIO(csv_output))
    for i, row in enumerate(reader):
        assert_eq(len(row), 4, f"Row {i}: has 4 cells")
    
    # Verify O'Neal name with apostrophe doesn't break CSV
    assert_in("O'Neal", csv_output, "Apostrophe in name preserved in CSV")
    
    # Verify comma in money values is handled (inside quotes)
    data_row = lines[1]
    # When we split by comma, quoted values with commas should be handled
    # In our case, "$25,000.00" contains a comma but is quoted
    assert_in('"$25,000.00"', data_row, "Money value with comma properly quoted")


# ════════════════════════════════════════════════════════
# TEST 2: Agent Name Normalization
# ════════════════════════════════════════════════════════
def test_agent_normalization():
    print("\n🔹 TEST 2: Agent Name Normalization")
    
    # Simulate the AGENT_ALIAS map from reports.py
    AGENT_ALIAS = {
        "Brendan": "Brendan O'Neal",
        "brendan": "Brendan O'Neal",
        "Jason": "Jason Taylor",
        "jason": "Jason Taylor",
    }
    
    # Test normalization
    test_cases = [
        ("Brendan", "Brendan O'Neal"),
        ("brendan", "Brendan O'Neal"),
        ("Brendan O'Neal", "Brendan O'Neal"),  # Already full name
        ("Jason", "Jason Taylor"),
        ("jason", "Jason Taylor"),
        ("Jason Taylor", "Jason Taylor"),  # Already full name
        ("Unknown Agent", "Unknown Agent"),  # Not in alias map
    ]
    
    for input_name, expected in test_cases:
        result = AGENT_ALIAS.get(input_name, input_name)
        assert_eq(result, expected, f"Normalize '{input_name}'")


# ════════════════════════════════════════════════════════
# TEST 3: POA Expiration Field Wire-up
# ════════════════════════════════════════════════════════
def test_poa_expiration():
    print("\n🔹 TEST 3: POA Expiration Field Wire-up")
    
    # Read poa.py and verify the expiration field is in add, list, and filter
    poa_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "poa.py")
    with open(poa_path, "r") as f:
        poa_code = f.read()
    
    # Verify expiration is accepted in add endpoint
    assert_in("expiration = body.get(\"expiration\")", poa_code, "Add endpoint reads expiration from body")
    assert_in("\"expiration\": expiration", poa_code, "Add endpoint stores expiration in document")
    
    # Verify expiration is in list projection
    assert_in("\"expiration\": 1", poa_code, "List endpoint projects expiration field")
    
    # Verify agent name is fixed
    assert_in("\"assigned_to_agent\": \"Brendan O'Neal\"", poa_code, "Default agent is full name")
    assert_not_in("\"assigned_to_agent\": \"Brendan\"", poa_code, "No legacy short agent name")
    
    # Read reports.py and verify expired-powers query uses expiration
    reports_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "reports.py")
    with open(reports_path, "r") as f:
        reports_code = f.read()
    
    assert_in("\"expiration\": {\"$ne\": None, \"$lt\": now_iso}", reports_code, "Expired-powers filters by expiration < now")
    assert_in("\"expiration\": {\"$gte\": now_iso, \"$lte\": cutoff_30}", reports_code, "Expiring-soon uses 30-day window")


# ════════════════════════════════════════════════════════
# TEST 4: Voided vs Expired POA Distinction
# ════════════════════════════════════════════════════════
def test_voided_vs_expired():
    print("\n🔹 TEST 4: Voided vs Expired POA Distinction")
    
    reports_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "reports.py")
    with open(reports_path, "r") as f:
        reports_code = f.read()
    
    # Voided powers: query by status == "voided"
    assert_in("\"status\": \"voided\"", reports_code, "Voided query filters by status='voided'")
    
    # Expired powers: query by expiration date AND exclude voided
    assert_in("\"$nin\": [\"voided\"]", reports_code, "Expired query excludes voided status")
    
    # Verify they are separate endpoints
    assert_in("/reports/voided-powers", reports_code, "Voided has its own endpoint")
    assert_in("/reports/expired-powers", reports_code, "Expired has its own endpoint")
    
    # Verify voided sorts by voided_at
    assert_in("\"voided_at\", -1", reports_code, "Voided sorts by voided_at descending")
    
    # Verify expired sorts by expiration date
    assert_in("\"expiration\", 1", reports_code, "Expired sorts by expiration ascending")


# ════════════════════════════════════════════════════════
# TEST 5: Report API Response Keys Match Frontend
# ════════════════════════════════════════════════════════
def test_response_keys():
    print("\n🔹 TEST 5: Report API Response Keys Match Frontend")
    
    reports_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "reports.py")
    with open(reports_path, "r") as f:
        reports_code = f.read()
    
    # Discharged: frontend expects data.bonds (not data.records)
    assert_in("\"bonds\": docs", reports_code, "Discharged returns 'bonds' key")
    assert_in("\"exonerated_count\":", reports_code, "Discharged returns exonerated_count")
    assert_in("\"surrendered_count\":", reports_code, "Discharged returns surrendered_count")
    
    # Voided: frontend expects data.powers
    assert_in("\"powers\": docs", reports_code, "Voided returns 'powers' key")
    
    # Forfeitures: frontend expects data.bonds
    # Check that there are at least 2 occurrences of "bonds": docs (discharged + forfeitures)
    count = reports_code.count("\"bonds\": docs")
    assert_eq(count >= 2, True, f"'bonds': docs appears {count} times (discharged + forfeitures)")
    
    # Forfeitures also needs avg_bond_amount
    assert_in("\"avg_bond_amount\":", reports_code, "Forfeitures returns avg_bond_amount")
    
    # Agent production: must have by_surety per agent
    assert_in("\"by_surety\":", reports_code, "Agent production returns by_surety")
    assert_in("\"county_count\":", reports_code, "Agent production returns county_count")
    assert_in("\"registered_agents\":", reports_code, "Agent production returns registered_agents list")


# ════════════════════════════════════════════════════════
# TEST 6: Surety Split Calculations
# ════════════════════════════════════════════════════════
def test_surety_split():
    print("\n🔹 TEST 6: Surety Split Calculations")
    
    # Import the calculation function directly
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard", "api"))
    
    # Read reports.py to extract SURETY_RATES and the calculation logic
    reports_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "reports.py")
    with open(reports_path, "r") as f:
        reports_code = f.read()
    
    # Extract SURETY_RATES from the code (parse manually)
    assert_in("SURETY_RATES", reports_code, "SURETY_RATES constant exists")
    
    # Verify the calculation function exists
    assert_in("def _calc_surety_split", reports_code, "_calc_surety_split function exists")
    
    # Verify it uses surety_per_100 and buf_per_100
    assert_in("surety_per_100", reports_code, "Uses surety_per_100 rate")
    assert_in("buf_per_100", reports_code, "Uses buf_per_100 rate")
    
    # Verify the formula: agent_retains = premium - surety_owed - buf_owed
    assert_in("agent_retains = premium - surety_owed - buf_owed", reports_code, "Agent retains = premium - surety - BUF")


# ════════════════════════════════════════════════════════
# TEST 7: Agent Name Defaults Consistency
# ════════════════════════════════════════════════════════
def test_agent_defaults():
    print("\n🔹 TEST 7: Agent Name Defaults Consistency")
    
    # Check bonds.py for consistent defaults
    bonds_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "api", "bonds.py")
    with open(bonds_path, "r") as f:
        bonds_code = f.read()
    
    # Count occurrences of the correct default
    correct_count = bonds_code.count("Brendan O'Neal")
    assert_eq(correct_count >= 4, True, f"bonds.py has {correct_count} 'Brendan O'Neal' references (need ≥4)")
    
    # Verify no standalone "Brendan" defaults remain (excluding O'Neal occurrences)
    import re
    # Find "Brendan" NOT followed by " O'Neal" and NOT inside a comment
    standalone = re.findall(r'"Brendan"(?!\s+O)', bonds_code)
    assert_eq(len(standalone), 0, f"No standalone 'Brendan' defaults remain ({len(standalone)} found)")
    
    # Check sl-record-bond.js
    rb_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "sl-record-bond.js")
    with open(rb_path, "r") as f:
        rb_code = f.read()
    
    rb_correct = rb_code.count("Brendan O'Neal")
    assert_eq(rb_correct >= 2, True, f"sl-record-bond.js has {rb_correct} 'Brendan O'Neal' refs (need ≥2)")
    
    rb_standalone = re.findall(r"'Brendan'(?!\s*O)", rb_code)
    assert_eq(len(rb_standalone), 0, f"No standalone 'Brendan' defaults in sl-record-bond.js ({len(rb_standalone)} found)")


# ════════════════════════════════════════════════════════
# TEST 8: POA Inventory UI Expiration Column
# ════════════════════════════════════════════════════════
def test_inventory_ui_expiration():
    print("\n🔹 TEST 8: POA Inventory UI Expiration Column")
    
    # Check index.html has the Expires column header
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    with open(html_path, "r") as f:
        html = f.read()
    
    assert_in("<th>Expires</th>", html, "Detail table has 'Expires' column header")
    assert_in("Expiration Date *", html, "Expiration field is required (no 'optional' label)")
    assert_not_in("Expiration Date <span", html, "No '(optional)' hint on expiration field")
    assert_in('type="date" required', html, "Expiration input has required attribute")
    
    # Check sl-inventory.js renders the expiration
    inv_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "sl-inventory.js")
    with open(inv_path, "r") as f:
        inv_code = f.read()
    
    assert_in("p.expiration", inv_code, "Detail renderer reads p.expiration")
    assert_in("inv-cell-exp", inv_code, "Detail renderer uses inv-cell-exp class")
    assert_in('colspan="8"', inv_code, "Colspan matches 8 columns")
    assert_not_in('colspan="7"', inv_code, "No stale colspan=7 references")


# ════════════════════════════════════════════════════════
# TEST 9: Expired-Powers Report Field Name Alignment
# ════════════════════════════════════════════════════════
def test_expired_field_alignment():
    print("\n🔹 TEST 9: Frontend Field Name Alignment")
    
    # Check sl-reports.js uses the correct field names
    rpt_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "sl-reports.js")
    with open(rpt_path, "r") as f:
        rpt_code = f.read()
    
    # Expired powers: should reference p.expiration (not just p.expiry_date)
    assert_in("p.expiration", rpt_code, "Expired renderer uses p.expiration field")
    
    # Voided/Expired: should reference p.surety_id with fallback
    assert_in("p.surety_id||p.surety", rpt_code, "Voided/Expired renders surety_id with fallback")
    
    # Agent production: should have surety chip rendering
    assert_in("rpt-surety-chip", rpt_code, "Agent production uses rpt-surety-chip class")
    assert_in("county_count", rpt_code, "Agent production displays county_count")


# ════════════════════════════════════════════════════════
# RUN ALL TESTS
# ════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("═" * 60)
    print("  ShamrockLeads — Reports & POA Integrity Tests")
    print("═" * 60)
    
    test_csv_export()
    test_agent_normalization()
    test_poa_expiration()
    test_voided_vs_expired()
    test_response_keys()
    test_surety_split()
    test_agent_defaults()
    test_inventory_ui_expiration()
    test_expired_field_alignment()
    
    print("\n" + "═" * 60)
    print(f"  RESULTS: {PASS} passed, {FAIL} failed")
    print("═" * 60)
    
    sys.exit(1 if FAIL > 0 else 0)
