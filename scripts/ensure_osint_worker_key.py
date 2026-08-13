#!/usr/bin/env python3
"""Mint OSINT_WORKER_KEY (and TRAPE_SERVER_URL) if missing.

Never overwrites a live key. Used by deploy and local setup so the
dashboard ↔ osint-worker shared secret cannot go empty in production.

Usage:
  python scripts/ensure_osint_worker_key.py
  python scripts/ensure_osint_worker_key.py --env-file /opt/shamrock-leads/.env
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

DEFAULT_TRAPE = "https://leads.shamrockbailbonds.biz"


def _upsert(text: str, key: str, value: str, comment: str | None = None) -> tuple[str, bool, str]:
    """Set KEY=value only when missing or blank. Returns (text, wrote, current)."""
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    match = pat.search(text)
    line = f"{key}={value}"
    if match:
        current = match.group(0).split("=", 1)[1].strip().strip("'").strip('"')
        if current:
            return text, False, current
        return pat.sub(line, text, count=1), True, value
    suffix = "\n"
    if comment:
        suffix += f"# {comment}\n"
    suffix += f"{line}\n"
    return text.rstrip() + suffix, True, value


def ensure(env_path: Path) -> dict[str, object]:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""

    minted_key = False
    key_val = secrets.token_hex(32)
    text, wrote_key, key_val = _upsert(
        text,
        "OSINT_WORKER_KEY",
        key_val,
        "OSINT worker shared secret (dashboard <-> osint-worker)",
    )
    minted_key = wrote_key

    text, wrote_trape, trape_val = _upsert(
        text,
        "TRAPE_SERVER_URL",
        DEFAULT_TRAPE,
        "Trape lure base (dashboard /track/{session})",
    )

    if wrote_key or wrote_trape or not env_path.is_file():
        env_path.write_text(text, encoding="utf-8")

    return {
        "env_file": str(env_path),
        "key_minted": minted_key,
        "key_len": len(key_val),
        "trape_wrote": wrote_trape,
        "trape_url": trape_val,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure OSINT_WORKER_KEY exists")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="Path to .env (default: repo .env)",
    )
    args = parser.parse_args()
    result = ensure(args.env_file)
    action = "minted" if result["key_minted"] else "kept"
    print(
        f"OSINT_WORKER_KEY {action} len={result['key_len']} "
        f"trape={'wrote' if result['trape_wrote'] else 'kept'} "
        f"file={result['env_file']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
