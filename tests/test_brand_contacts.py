"""Brand-contact guard: wrong domains and Shamrock-shaped phones fail CI."""
from __future__ import annotations

from pathlib import Path

from scripts.check_brand_contacts import (
    CANONICAL_PHONES,
    PIN_AS_PHONE,
    WRONG_DOMAINS,
    scan_text,
    scan_tree,
    shamrock_shaped_phone,
)


def test_canonical_phones_and_domain_are_clean():
    text = "\n".join([
        "Call (239) 332-2245 or 239-332-BAIL.",
        "Text +1 727-295-2245.",
        "Office line 2399550178.",
        "Email admin@shamrockbailbonds.biz",
        "https://leads.shamrockbailbonds.biz/health",
        "https://paperwork.shamrockbailbonds.biz/health/live",
    ])
    assert scan_text("dashboard/example.py", text) == []
    assert shamrock_shaped_phone("2393322245") is None
    assert set(CANONICAL_PHONES) == {"2393322245", "7272952245", "2399550178"}


def test_wrong_domains_are_flagged_without_matching_the_real_domain():
    text = "\n".join([
        "https://shamrockbailbonds.com/intake",
        "see shamrockbail.com and shamrockbail.biz",
        "admin@shamrockbailbonds.com",
    ])
    findings = scan_text("dashboard/routers/example.py", text)
    domains = {item.detail for item in findings}
    assert domains == set(WRONG_DOMAINS)
    assert scan_text("docs/note.md", "https://www.shamrockbailbonds.biz") == []


def test_one_digit_typo_and_pin_phone_fail_placeholders_do_not():
    bad = scan_text("dashboard/sl-help.js", "Call (239) 334-2245 today")
    assert len(bad) == 1
    assert bad[0].kind == "shamrock_phone"

    pin = scan_text("dashboard/sl-active-bonds.js", "Call us at (239) 224-5454.")
    assert len(pin) == 1
    assert PIN_AS_PHONE == "2392245454"

    assert scan_text("dashboard/index.html", 'placeholder="(239) 555-0178"') == []
    assert scan_text("dashboard/extensions.py", "(239) 955-0314") == []
    assert scan_text("docs/runbooks/bluebubbles-tunnel.md", "(239) 955-0301") == []
    assert shamrock_shaped_phone("2395550178") is None


def test_tests_directory_is_not_scanned(tmp_path: Path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_bad.py").write_text("https://shamrockbailbonds.com/intake\n", encoding="utf-8")
    app = tmp_path / "dashboard"
    app.mkdir()
    (app / "app.py").write_text("phone (239) 332-2245\n", encoding="utf-8")
    assert scan_tree(tmp_path) == []


def test_repo_has_no_brand_contact_violations():
    findings = scan_tree()
    assert findings == [], "\n".join(item.format() for item in findings)
