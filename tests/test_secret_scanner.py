"""
Unit tests for deterministic redacted secret scanner and truthful ecosystem secrets auditor.
Verifies that:
  1. Synthetic secrets are detected and properly masked (never echoed in plain text).
  2. Safe placeholders and documented environment variable names are allowed.
  3. Absent production environments produce truthful UNVERIFIED/NOT-PROVEN states.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from scripts.scan_secrets import (
    is_placeholder,
    mask_finding,
    scan_file,
)
from scripts.check_ecosystem_secrets import (
    check_keys,
    fingerprint,
)


def test_is_placeholder_rules():
    assert is_placeholder("REPLACE_WITH_OPENAI_KEY") is True
    assert is_placeholder("<YOUR_API_KEY>") is True
    assert is_placeholder("your_api_key_here") is True
    assert is_placeholder("mock_token_123") is True
    assert is_placeholder("test_secret_val") is True
    assert is_placeholder("masked") is True
    assert is_placeholder("fp:a61e521349") is True
    assert is_placeholder("GAS_API_KEY") is True
    assert is_placeholder("DOCUSEAL_API_KEY") is True

    # Real-looking synthetic tokens are NOT placeholders
    assert is_placeholder("sk-proj-abcdefghijklmnopqrstuvwxyz1234567890") is False
    assert is_placeholder("AKIA1234567890ABCDEF") is False


def test_mask_finding_redaction():
    fake_key = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    masked = mask_finding(fake_key)
    assert fake_key not in masked
    assert masked.startswith("sk-p...****")
    assert f"length: {len(fake_key)}" in masked


def test_scanner_detects_synthetic_secrets():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_code.py"
        test_file.write_text(
            '# Safe code\n'
            'API_KEY = os.getenv("OPENAI_API_KEY", "REPLACE_WITH_OPENAI_KEY")\n'
            '# Hardcoded leak:\n'
            'LEAKED_KEY = "sk-proj-9999999999999999999999999999999999999999"\n'
            'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n'
        )

        findings = scan_file(test_file)
        assert len(findings) == 2
        rule_ids = [f.rule_id for f in findings]
        assert "OPENAI_KEY" in rule_ids
        assert "AWS_ACCESS_KEY" in rule_ids

        # Ensure no raw secret leaked in findings
        for f in findings:
            assert "9999999999999999999999999999999999999999" not in str(f)
            assert "AKIAABCDEFGHIJKLMNOP" not in f.masked_summary


def test_scanner_ignores_placeholders_and_comments():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "safe_guide.md"
        test_file.write_text(
            '# Developer Guide\n'
            'Set your key in .env:\n'
            'OPENAI_API_KEY=REPLACE_WITH_OPENAI_KEY\n'
            'DOCUSEAL_API_KEY=<your_docuseal_api_key>\n'
            'SLACK_TOKEN=xoxb-dummy-token-placeholder\n'
        )

        findings = scan_file(test_file)
        assert len(findings) == 0


def test_ecosystem_checker_truthful_absent_reporting():
    verified, missing, unverified, lines = check_keys(
        label="shamrock-absent-test",
        env_file_present=False,
        env={},
        critical=["KEY_A", "KEY_B"],
        recommended=["KEY_C"],
    )

    assert verified == 0
    assert missing == 0
    assert unverified == 2
    assert any("NOT-PROVEN — local file absent" in line for line in lines)
