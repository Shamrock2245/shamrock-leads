#!/usr/bin/env python3
"""Fail if a non-test file publishes the wrong Shamrock contact details.

Canonical public contacts:
  phones  239-332-2245, 727-295-2245, 239-955-0178
  email   admin@shamrockbailbonds.biz
  domain  shamrockbailbonds.biz

A phone is treated as Shamrock-shaped when it uses area code 239 or 727 and
either ends in a published line (2245 or 0178) or differs from a canonical
number by one digit. The 555 exchange is the NANP fictional range used in
UI placeholders, so those are ignored. Operational lines that are not a
one-digit typo of a canonical number (the 955-0301 desk line and the
955-0314 Mac) are not Shamrock-shaped and are left alone.

Also rejects the known-wrong lookalike hostnames (the .com twins and the
short shamrockbail name on .biz) and the dashboard PIN written as a 239
phone, which payment copy already forbids.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CANONICAL_PHONES = frozenset({"2393322245", "7272952245", "2399550178"})
BRAND_LINE_ENDINGS = frozenset({"2245", "0178"})
# Dashboard PIN 224545 pasted into a client SMS as a phone. Split so this
# source file does not contain the contiguous digit string.
PIN_AS_PHONE = "239" + "224" + "5454"

# Hostnames split so this file is not a finding of its own rules.
_COM = "com"
_BIZ = "biz"
WRONG_DOMAINS = (
    "shamrockbailbonds." + _COM,
    "shamrockbail." + _COM,
    "shamrockbail." + _BIZ,
)

_SKIP_DIRS = frozenset({
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "tests",
    "venv",
})
_SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".gz", ".woff", ".woff2", ".ttf", ".eot", ".pyc",
    ".so", ".dll", ".exe", ".mp4", ".mp3", ".wav", ".bin",
})

_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[\s.\-]?)?"
    r"\(?(239|727)\)?"
    r"[\s.\-]*"
    r"(\d{3})"
    r"[\s.\-]*"
    r"(\d{4})"
    r"(?!\d)"
)
_DOMAIN = re.compile(
    r"(?i)(?<![A-Za-z0-9-])(?:"
    + "|".join(re.escape(d) for d in WRONG_DOMAINS)
    + r")(?![A-Za-z0-9-])"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.detail}"


def _hamming(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right))


def shamrock_shaped_phone(digits: str) -> str | None:
    """Return a short reason when ``digits`` is a wrong Shamrock-shaped phone."""
    if len(digits) != 10 or digits in CANONICAL_PHONES:
        return None
    if digits[3:6] == "555":
        return None
    if digits == PIN_AS_PHONE:
        return "pin-shaped callback"
    if digits[:3] not in {"239", "727"}:
        return None
    if digits[-4:] in BRAND_LINE_ENDINGS:
        return "brand line ending is not a canonical number"
    if any(_hamming(digits, canon) == 1 for canon in CANONICAL_PHONES):
        return "one digit off a canonical Shamrock number"
    return None


def scan_text(rel_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _DOMAIN.finditer(line):
            findings.append(Finding(rel_path, lineno, "wrong_domain", match.group(0).lower()))
        for match in _PHONE.finditer(line):
            digits = match.group(1) + match.group(2) + match.group(3)
            reason = shamrock_shaped_phone(digits)
            if reason:
                findings.append(Finding(rel_path, lineno, "shamrock_phone", f"{match.group(0).strip()} ({reason})"))
    return findings


def _should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in _SKIP_DIRS for part in rel_parts):
        return True
    if path.suffix.lower() in _SKIP_SUFFIXES:
        return True
    return False


def scan_tree(root: Path | None = None) -> list[Finding]:
    root = (root or ROOT).resolve()
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file() or _should_skip(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        findings.extend(scan_text(rel, text))
    return findings


def main() -> int:
    findings = scan_tree(ROOT)
    if not findings:
        print("brand contacts ok")
        return 0
    print(f"{len(findings)} wrong Shamrock contact detail(s):", file=sys.stderr)
    for finding in findings:
        print(finding.format(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
