"""Pause / resume plan for the installment invoice job (read only).

Brendan (Nov 4, 2026): installment texts stay paused during T7. This script
never writes. It reads the ``installment_invoices`` automation switch and
counts the scheduled premium-confirmation installments that the next run
would stage, then prints the staff action that pauses or resumes the job.

The action itself is the existing, audited dashboard toggle (staff session,
no machine key): ``POST /api/automation/toggle/installment_invoices`` with
``{"enabled": false}`` to pause or ``{"enabled": true}`` to resume. That
toggle records ``updated_by`` on the automation config. This script does not
call it.

Read only. The connection string is ``SHAMROCK_MONGO_RO_URI`` and nothing
else. Prints counts only: no names, phones, booking numbers, or links.

Usage:
    SHAMROCK_MONGO_RO_URI='mongodb+srv://readonly...' \\
        python scripts/installment_invoices_pause.py --plan pause
    python scripts/installment_invoices_pause.py --plan resume --offline
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

JOB_KEY = "installment_invoices"
TOGGLE_PATH = f"/api/automation/toggle/{JOB_KEY}"
RO_URI_ENV = "SHAMROCK_MONGO_RO_URI"


def summarize(config: Optional[dict], plans: Iterable[dict], today: str) -> Dict[str, Any]:
    """Counts only. ``config`` is the ``automation_master`` doc (or None)."""
    section = (config or {}).get(JOB_KEY) if isinstance(config, dict) else None
    enabled = None
    if isinstance(section, dict) and "enabled" in section:
        enabled = bool(section.get("enabled"))
    due = later = staged = 0
    plan_count = 0
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        plan_count += 1
        for row in plan.get("installments") or []:
            if not isinstance(row, dict):
                continue
            if row.get("status") != "scheduled" or row.get("invoice_key"):
                staged += 1
                continue
            when = str(row.get("due_date") or "")
            if when and when <= today:
                due += 1
            elif when:
                later += 1
    return {
        "enabled": enabled,
        "active_plans": plan_count,
        "due_now": due,
        "scheduled_later": later,
        "already_staged_or_closed": staged,
    }


def action_for(plan: str, enabled: Optional[bool]) -> Dict[str, Any]:
    want = plan == "resume"
    if enabled is not None and enabled == want:
        return {"needed": False, "note": f"already {'enabled' if want else 'paused'}"}
    return {
        "needed": True,
        "method": "POST",
        "path": TOGGLE_PATH,
        "body": {"enabled": want},
        "auth": "staff dashboard session (machine keys are refused)",
    }


def _read(uri: str) -> tuple:
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        db = client.get_default_database()
        config = db["automation_config"].find_one(
            {"type": "automation_master"}, {"_id": 0, JOB_KEY: 1},
        )
        plans = list(db["payment_plans"].find(
            {"status": "active", "source": "premium_confirmation"},
            {"_id": 0, "installments.status": 1, "installments.due_date": 1,
             "installments.invoice_key": 1},
        ))
        return config, plans
    finally:
        client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", choices=("pause", "resume"), required=True)
    parser.add_argument("--offline", action="store_true",
                        help="Do not connect; print only the staff action.")
    args = parser.parse_args(argv)
    today = datetime.now(timezone.utc).date().isoformat()

    summary: Dict[str, Any] = {"enabled": None}
    if not args.offline:
        uri = (os.getenv(RO_URI_ENV) or "").strip()
        if not uri:
            print(f"{RO_URI_ENV} is not set; rerun with --offline or a read-only URI.",
                  file=sys.stderr)
            return 2
        config, plans = _read(uri)
        summary = summarize(config, plans, today)
        for key, value in summary.items():
            print(f"{key}: {value}")
    action = action_for(args.plan, summary.get("enabled"))
    print(f"plan: {args.plan}")
    if not action["needed"]:
        print(f"no action: {action['note']}")
        return 0
    print(f"staff action: {action['method']} {action['path']} {action['body']}")
    print(f"auth: {action['auth']}")
    print("This script wrote nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
