#!/usr/bin/env python3
"""
Shamrock Ecosystem — Deterministic Secret Scanner
=================================================
Scans source files, documentation, fixtures, and configurations for potential
hardcoded secret material prior to git commits or CI publication.

Security Directives:
  1. NEVER print matched secret values or reconstructible fragments to stdout/stderr.
  2. Output only file path, line number, rule identifier, and safe masked prefix.
  3. Fail closed on high-entropy structured secrets.
  4. Allow documented environment variable names, harmless placeholders, and non-secret hashes.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

# Ignore directories & file extensions
IGNORE_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".gemini",
    "dist",
    "build",
    ".turbo",
    ".next",
    "coverage",
}

IGNORE_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".pdf", ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll",
    ".zip", ".tar", ".gz", ".lock",
}

IGNORE_FILES: Set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    ".env",           # Local env files are gitignored and checked via check_ecosystem_secrets
    ".env.local",
    ".env.production",
}

# Known harmless placeholder values allowed in code and documentation
SAFE_PLACEHOLDER_PATTERNS: List[re.Pattern] = [
    re.compile(r"^REPLACE_WITH_[A-Z0-9_]+$"),
    re.compile(r"^<[a-zA-Z0-9_\-\s]+>$"),
    re.compile(r"^YOUR_[A-Z0-9_]+_HERE$", re.IGNORECASE),
    re.compile(r"^your[_\-][a-zA-Z0-9_\-]+[_\-]here$", re.IGNORECASE),
    re.compile(r"^mock[_\-][a-zA-Z0-9_\-]+$", re.IGNORECASE),
    re.compile(r"^test[_\-][a-zA-Z0-9_\-]+$", re.IGNORECASE),
    re.compile(r"^dummy[_\-][a-zA-Z0-9_\-]+$", re.IGNORECASE),
    re.compile(r"^\*{3,}$"),
    re.compile(r"^\.{3,}$"),
    re.compile(r"^masked$", re.IGNORECASE),
    re.compile(r"^none$", re.IGNORECASE),
    re.compile(r"^undefined$", re.IGNORECASE),
]

# Secret Pattern Rule Definitions
class SecretRule(NamedTuple):
    rule_id: str
    description: str
    pattern: re.Pattern
    min_length: int = 8


SECRET_RULES: List[SecretRule] = [
    SecretRule(
        "PRIVATE_KEY",
        "Private Key Header",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        min_length=25,
    ),
    SecretRule(
        "OPENAI_KEY",
        "OpenAI / GenAI API Key",
        re.compile(r"\b(?:sk-[a-zA-Z0-9_-]{20,}|sk-proj-[a-zA-Z0-9_-]{30,})\b"),
        min_length=20,
    ),
    SecretRule(
        "ANTHROPIC_KEY",
        "Anthropic API Key",
        re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"),
        min_length=20,
    ),
    SecretRule(
        "AWS_ACCESS_KEY",
        "AWS Access Key ID",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        min_length=20,
    ),
    SecretRule(
        "GOOGLE_API_KEY",
        "Google Cloud API Key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        min_length=39,
    ),
    SecretRule(
        "SLACK_TOKEN",
        "Slack Bot/User Token",
        re.compile(r"\bxox[baprs]-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{20,}\b"),
        min_length=30,
    ),
    SecretRule(
        "GITHUB_PAT",
        "GitHub Personal Access Token",
        re.compile(r"\b(?:ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b"),
        min_length=40,
    ),
    SecretRule(
        "TWILIO_AUTH_TOKEN",
        "Twilio Live Auth Assignment",
        re.compile(r"""(?:TWILIO_AUTH_TOKEN|twilio_auth_token)\s*=\s*['"]([a-f0-9]{32})['"]"""),
        min_length=32,
    ),
    SecretRule(
        "HIGH_ENTROPY_PASSWORD_ASSIGN",
        "Hardcoded Production Password Literal",
        re.compile(r"""(?i)(?:password|secret_key|api_key|webhook_secret)\s*[:=]\s*["']([a-zA-Z0-9!@#$%^&*()_+=\-]{24,})["']"""),
        min_length=24,
    ),
]


def is_placeholder(token: str) -> bool:
    """Check if token matches safe placeholder rules."""
    s = token.strip().strip("'\"")
    if not s:
        return True
    # Documented env var names themselves are not secrets
    if re.match(r"^[A-Z0-9_]+(?:_API_KEY|_SECRET|_TOKEN|_PASSWORD|_URL|_ID|_KEY)$", s):
        return True
    for p in SAFE_PLACEHOLDER_PATTERNS:
        if p.match(s):
            return True
    # Non-secret fingerprint prefixes
    if s.startswith("fp:") or s.startswith("sha256:") or s.startswith("corr_") or s.startswith("task-"):
        return True
    return False


def mask_finding(match_str: str) -> str:
    """Mask matched secret so it cannot be reconstructed."""
    clean = match_str.strip().strip("'\"")
    if len(clean) <= 6:
        return "***"
    prefix = clean[:4]
    return f"{prefix}...**** (length: {len(clean)})"


class ScanFinding(NamedTuple):
    file_path: str
    line_number: int
    rule_id: str
    description: str
    masked_summary: str


def scan_file(file_path: Path) -> List[ScanFinding]:
    """Scan a single file for secret patterns."""
    findings: List[ScanFinding] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return findings

    for line_idx, line in enumerate(content.splitlines(), start=1):
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("//") or line_clean.startswith("#"):
            # If the comment is explaining placeholders, check if it's safe
            if "REPLACE_WITH" in line_clean or "placeholder" in line_clean.lower() or "example" in line_clean.lower():
                continue

        for rule in SECRET_RULES:
            for match in rule.pattern.finditer(line):
                # Handle group 1 if present (e.g. from key = "value" regex)
                matched_token = match.group(1) if match.groups() else match.group(0)
                if len(matched_token) < rule.min_length:
                    continue
                if is_placeholder(matched_token):
                    continue

                findings.append(ScanFinding(
                    file_path=str(file_path),
                    line_number=line_idx,
                    rule_id=rule.rule_id,
                    description=rule.description,
                    masked_summary=mask_finding(matched_token),
                ))
    return findings


def scan_directory(root_dir: Path, target_extensions: Optional[Set[str]] = None) -> List[ScanFinding]:
    """Recursively scan a directory for secrets."""
    all_findings: List[ScanFinding] = []
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Exclude ignored directories in-place
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        for fname in filenames:
            if fname in IGNORE_FILES:
                continue
            ext = os.path.splitext(fname)[-1].lower()
            if ext in IGNORE_EXTENSIONS:
                continue
            if target_extensions and ext not in target_extensions:
                continue

            full_path = Path(dirpath) / fname
            findings = scan_file(full_path)
            all_findings.extend(findings)

    return all_findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Redacted Secret Scanner for Shamrock Repositories")
    parser.add_argument("paths", nargs="*", default=["."], help="Paths or directories to scan (default: current directory)")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if any secrets are detected")
    args = parser.parse_args()

    total_findings: List[ScanFinding] = []

    for p in args.paths:
        target = Path(p).resolve()
        if target.is_file():
            total_findings.extend(scan_file(target))
        elif target.is_dir():
            total_findings.extend(scan_directory(target))

    print(f"\n🔒 Shamrock Redacted Secret Scanner")
    print(f"{'═' * 60}")
    print(f"  Scanned targets : {', '.join(args.paths)}")
    print(f"  Total findings  : {len(total_findings)}")
    print(f"{'═' * 60}")

    if not total_findings:
        print("  ✅ Zero exposed secret patterns detected.\n")
        return 0

    print("  ❌ POTENTIAL EXPOSED CREDENTIALS DETECTED (Redacted for Security):")
    for f in total_findings:
        rel_path = f.file_path
        try:
            rel_path = str(Path(f.file_path).relative_to(Path.cwd()))
        except Exception:
            pass
        print(f"    • {rel_path}:{f.line_number} — [{f.rule_id}] {f.description} ({f.masked_summary})")

    print(f"\n{'─' * 60}")
    print("  Remediation: Move secret literals to environment variables (.env, Script Properties,")
    print("  or Wix Secrets Manager) and replace code literals with safe placeholders (REPLACE_WITH_*).")
    print("  See: docs/ops/DEVELOPER_SECRET_HYGIENE_GUIDE.md")
    print(f"{'─' * 60}\n")

    if args.strict and total_findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
