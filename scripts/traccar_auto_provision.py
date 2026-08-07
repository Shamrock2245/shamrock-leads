#!/usr/bin/env python3
"""
ShamrockLeads — Traccar Device Auto-Provisioning & Quick-Setup Script
═══════════════════════════════════════════════════════════════════════
Automates device registration on the Traccar server, verifies OsmAnd
port 5055 connectivity, generates 1-click configuration deep-links &
setup URLs for Traccar Client app, and dispatches SMS instructions.

Usage:
    python scripts/traccar_auto_provision.py --booking LEE-2026-00123 --name "John Doe" --county "Lee"
    python scripts/traccar_auto_provision.py --unique-id shamrock-LEE-2026-00123 --phone "2395550100" --send-sms
"""

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

# Ensure repository root is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def generate_traccar_config(unique_id: str, public_host: str = None, port: str = "5055", frequency: int = 60) -> dict:
    """
    Generate Traccar configuration credentials, deep links, and setup URL.
    """
    host = public_host or os.getenv("TRACCAR_PUBLIC_HOST", "leads.shamrockbailbonds.biz")
    server_url = f"http://{host}:{port}"
    
    # Deep link for 1-click auto-configuration inside Traccar Client app
    # Format: org.traccar.client://?url=SERVER_URL&id=DEVICE_ID&frequency=FREQUENCY
    params = {
        "url": server_url,
        "id": unique_id,
        "frequency": str(frequency),
        "distance": "100",
        "angle": "30",
    }
    deeplink = f"org.traccar.client://?{urllib.parse.urlencode(params)}"
    
    dashboard_url = os.getenv("DASHBOARD_PUBLIC_URL", f"https://{host}").rstrip("/")
    setup_url = f"{dashboard_url}/traccar/setup/{unique_id}"
    
    return {
        "unique_id": unique_id,
        "server_url": server_url,
        "frequency_seconds": frequency,
        "deeplink": deeplink,
        "setup_url": setup_url,
    }


def main():
    parser = argparse.ArgumentParser(description="Provision Traccar device & generate setup links")
    parser.add_argument("--booking", help="Booking number (e.g. LEE-2026-00123)")
    parser.add_argument("--unique-id", help="Explicit Traccar uniqueId (e.g. shamrock-LEE-00123)")
    parser.add_argument("--name", default="Defendant", help="Defendant full name")
    parser.add_argument("--county", default="Lee", help="County")
    parser.add_argument("--phone", default="", help="Defendant phone number for SMS dispatch")
    parser.add_argument("--send-sms", action="store_true", help="Send setup link via BlueBubbles iMessage/SMS")
    parser.add_argument("--json", action="store_true", help="Output JSON result only")

    args = parser.parse_args()

    if not args.booking and not args.unique_id:
        print("❌ Error: Must specify --booking or --unique-id", file=sys.stderr)
        sys.exit(1)

    from dashboard.services.traccar_client import booking_to_unique_id, get_traccar_client

    unique_id = args.unique_id or booking_to_unique_id(args.booking)
    config = generate_traccar_config(unique_id)

    if not args.json:
        print("════════════════════════════════════════════════════════")
        print("  ShamrockLeads — Traccar Device Provisioning")
        print("════════════════════════════════════════════════════════")
        print(f"  Device Name:   {args.name} ({args.county})")
        print(f"  Unique ID:     {unique_id}")
        print(f"  Server URL:    {config['server_url']}")
        print(f"  Frequency:     {config['frequency_seconds']} seconds")
        print(f"  Setup URL:     {config['setup_url']}")
        print(f"  App Deep Link: {config['deeplink']}")
        print("════════════════════════════════════════════════════════\n")

    # Call Traccar API to ensure device is registered
    try:
        import asyncio
        tc = get_traccar_client()
        device_name = f"{args.name} — {args.county}"[:120]
        device = asyncio.run(tc.ensure_device(unique_id, name=device_name, phone=args.phone))
        config["traccar_device_id"] = device.get("id")
        config["registered"] = True
        if not args.json:
            print(f"✅ Device registered in Traccar API (ID: {device.get('id')})")
    except Exception as e:
        config["registered"] = False
        config["error"] = str(e)
        if not args.json:
            print(f"⚠️ Traccar API registration notice: {e}")

    # Send setup text via BlueBubbles if requested
    if args.send_sms and args.phone:
        try:
            from dashboard.services.bb_client import send_message_universal
            msg = (
                f"Hi {args.name} — Shamrock Bail Bonds GPS Setup:\n\n"
                f"1) Download 'Traccar Client' from App Store / Google Play\n"
                f"2) Open 1-Click Setup: {config['setup_url']}\n"
                f"3) Tap 'Auto-Configure' and turn Service ON.\n\n"
                f"Questions? (239) 332-2245 ☘️"
            )
            import asyncio
            from dashboard.services.bb_client import bb_send_accepted, normalize_bb_send_result
            sms_res = normalize_bb_send_result(asyncio.run(send_message_universal(args.phone, msg)))
            config["sms_sent"] = bb_send_accepted(sms_res)
            config["sms_channel"] = sms_res.get("channel")
            if not args.json:
                print(f"📱 BlueBubbles setup text dispatch: {'✅ Sent/Queued' if config['sms_sent'] else '❌ Failed'}")
        except Exception as e:
            config["sms_sent"] = False
            config["sms_error"] = str(e)
            if not args.json:
                print(f"⚠️ SMS dispatch error: {e}")

    if args.json:
        print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
