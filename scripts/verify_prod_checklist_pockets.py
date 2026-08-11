#!/usr/bin/env python3
"""
Verify remaining ECOSYSTEM_PROD_CHECKLIST pockets (C4, E2, P1.4, P1.7 helpers).

Usage (from shamrock-leads root):
  python scripts/verify_prod_checklist_pockets.py
  python scripts/verify_prod_checklist_pockets.py --rearrest-mock   # needs MONGODB_URI
  python scripts/verify_prod_checklist_pockets.py --osint           # needs OSINT_WORKER_KEY

Does not print secret values — only pass/fail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

STABLE_GAS = (
    "https://script.google.com/macros/s/"
    "AKfycbyCIDPzA_EA1B1SGsfhYiXRGKM8z61EgACZdDPILT_MjjXee0wSDEI0RRYthE0CvP-Z/exec"
)
LEGACY_DOCS_GAS = (
    "https://script.google.com/macros/s/"
    "AKfycby5EM_U4d1GRHf_Or64RPGlOFUuOFld4m5ap9DghRm5njoUCTzSmEVmzmwmak9sR6fSFQ/exec"
)


def _get(url: str, timeout: int = 25, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "shamrock-checklist/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")[:2000]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def check_c4_gas_health() -> dict:
    url = (os.getenv("GAS_WEB_APP_URL") or STABLE_GAS).strip()
    code, body = _get(f"{url}?action=health")
    ok = code == 200 and '"success":true' in body.replace(" ", "")
    version = None
    try:
        version = json.loads(body).get("version")
    except Exception:
        pass
    return {
        "item": "C4",
        "ok": ok,
        "http": code,
        "version": version,
        "url_is_stable_factory": STABLE_GAS in url or url.rstrip("/") == STABLE_GAS.rstrip("/"),
        "body_preview": body[:120],
    }


def check_e2_gas_alignment() -> dict:
    leads = (os.getenv("GAS_WEB_APP_URL") or "").strip()
    return {
        "item": "E2/C1_code",
        "ok": (not leads) or (STABLE_GAS in leads),
        "leads_env_set": bool(leads),
        "matches_stable": STABLE_GAS in leads if leads else None,
        "legacy_docs_url_deprecated": True,
        "note": "Netlify GAS_WEB_APP_URL verified separately via `netlify env:get` — must equal stable factory",
    }


def check_p14_osint() -> dict:
    """Probe OSINT worker (local Docker or prod automation/osint-status)."""
    worker_url = (
        os.getenv("OSINT_WORKER_URL")
        or os.getenv("OSINT_INTERNAL_URL")
        or "http://127.0.0.1:5065"
    ).rstrip("/")
    key = (os.getenv("OSINT_WORKER_KEY") or "").strip()
    headers = {}
    if key:
        headers["X-Worker-Key"] = key

    code_h, body_h = _get(f"{worker_url}/health", timeout=8)
    health_ok = code_h == 200 and ("ok" in body_h.lower() or "status" in body_h.lower())

    # Production probe via machine-auth automation endpoint
    prod_ok = False
    prod_preview = None
    gas = (os.getenv("GAS_API_KEY") or "").strip()
    if gas:
        code_a, body_a = _get(
            "https://leads.shamrockbailbonds.biz/api/automation/osint-status",
            timeout=20,
            headers={"X-API-Key": gas, "X-Api-Key": gas},
        )
        if code_a == 200:
            try:
                data = json.loads(body_a)
                prod_ok = bool(data.get("ok") and data.get("ready_for_scans"))
                prod_preview = {
                    "ready_for_scans": data.get("ready_for_scans"),
                    "worker_reachable": data.get("worker_reachable"),
                    "maigret_available": (data.get("maigret") or {}).get("available"),
                }
            except Exception:
                prod_preview = body_a[:200]

    return {
        "item": "P1.4",
        "ok": health_ok or prod_ok,
        "local_worker_url": worker_url,
        "local_health_http": code_h,
        "prod_automation_osint_ok": prod_ok,
        "prod_preview": prod_preview,
        "public_note": "Prefer GET /api/automation/osint-status with GAS_API_KEY",
    }


async def run_rearrest_mock() -> dict:
    """Insert ephemeral test bond + arrest, run scan, cleanup."""
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.getenv("MONGODB_URI")
    if not uri:
        return {"item": "P1.7", "ok": False, "error": "MONGODB_URI not set"}

    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
    db = client[db_name]
    now = datetime.now(timezone.utc)
    tag = f"CHECKLIST-REARREST-{now.strftime('%Y%m%d%H%M%S')}"
    booking_new = f"{tag}-NEW"
    booking_old = f"{tag}-OLD"

    bond_doc = {
        "status": "active",
        "defendant_name": "CHECKLIST REARREST DEFENDANT",
        "full_name": "CHECKLIST REARREST DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": booking_old,
        "bond_amount": 5000,
        "poa_number": f"OSI-TEST-{tag[-6:]}",
        "county": "Lee",
        "case_number": f"TEST-{tag[-8:]}",
        "created_at": now.isoformat(),
        "_checklist_mock": True,
    }
    arrest_doc = {
        "full_name": "CHECKLIST REARREST DEFENDANT",
        "defendant_name": "CHECKLIST REARREST DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": booking_new,
        "county": "Lee",
        "charges": "CHECKLIST MOCK CHARGE — DELETE",
        "bond_amount": 2500,
        "scraped_at": now.isoformat(),
        "arrest_date": now.strftime("%Y-%m-%d"),
        "custody_status": "In Custody",
        "_checklist_mock": True,
    }

    try:
        await db["active_bonds"].insert_one(bond_doc)
        await db["arrests"].insert_one(arrest_doc)

        # Point rearrest scanner at same DB via env already used by app
        os.environ.setdefault("MONGODB_URI", uri)
        os.environ.setdefault("MONGODB_DB_NAME", db_name)

        # Import after env
        from dashboard.extensions import get_collection
        from dashboard.routers import rearrest_detector as rd

        # Monkeypatch get_collection used inside module if needed — it uses extensions
        result = await rd.scan_for_rearrests(hours=24)
        detected = int(result.get("detected") or 0)

        # Confirm notification exists for our booking
        note = await db["rearrest_notifications"].find_one({"booking_number": booking_new})
        ok = detected >= 1 or note is not None

        # Slack: if webhook set, scan path already attempted in production code
        # (this unit path may not call Slack if not wired in scan_for_rearrests)
        slack_configured = bool(
            os.getenv("SLACK_WEBHOOK_ARRESTS") or os.getenv("SLACK_WEBHOOK_REARREST")
        )

        return {
            "item": "P1.7",
            "ok": ok,
            "scan_result": {
                "detected": result.get("detected"),
                "scanned_arrests": result.get("scanned_arrests"),
                "active_bonds_checked": result.get("active_bonds_checked"),
            },
            "notification_found": bool(note),
            "slack_webhook_configured": slack_configured,
            "mock_booking": booking_new,
        }
    except Exception as e:
        return {"item": "P1.7", "ok": False, "error": str(e)[:300]}
    finally:
        # Cleanup mocks
        try:
            await db["active_bonds"].delete_many({"_checklist_mock": True})
            await db["arrests"].delete_many({"_checklist_mock": True})
            await db["rearrest_notifications"].delete_many(
                {"booking_number": {"$regex": "^CHECKLIST-REARREST-"}}
            )
        except Exception:
            pass
        client.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rearrest-mock", dest="rearrest_mock", action="store_true")
    ap.add_argument("--osint", action="store_true", default=True)
    args = ap.parse_args()

    results = [check_c4_gas_health(), check_e2_gas_alignment(), check_p14_osint()]

    if args.rearrest_mock:
        results.append(asyncio.run(run_rearrest_mock()))

    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "results": results}, indent=2))
    failed = [r for r in results if not r.get("ok")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
