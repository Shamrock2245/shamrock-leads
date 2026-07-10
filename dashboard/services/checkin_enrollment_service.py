"""
ShamrockLeads — Check-In Enrollment (A + C + Traccar GPS)

Transparent, consent-based bond monitoring:
  - Enable check_in_required on active_bonds after signing (or staff action)
  - Generate defendant portal magic link
  - Provision Traccar device (in-stack GPS — not a third-party vendor)
  - Portal check-ins inject one-shot GPS into Traccar (OsmAnd)
  - Continuous GPS = defendant installs Traccar Client (explicit, elevated)
  - Staff-triggered send of check-in link via iMessage/SMS (human gate)

Policy: docs/policies/monitoring-checkin-policy.md
Never log full phone numbers.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

CONSENT_VERSION = "checkin-v1-2026-07"
DEFAULT_FREQUENCY_DAYS = 7
CONDITION_SUMMARY = (
    "As a condition of your bail bond, you agree to complete scheduled check-ins "
    "with Shamrock Bail Bonds. Each check-in may include confirmation of your contact "
    "information and voluntary location verification only when you tap Check In and "
    "grant permission. Elevated cases may require installing our tracking app "
    "(Traccar Client) with your knowledge. Missed check-ins may increase supervision "
    "or lead to bond remedies."
)

# Short copy for staff SMS/iMessage (no covert framing)
CHECKIN_MESSAGE_TEMPLATE = (
    "Hi {name} — Shamrock Bail Bonds. Per your bond conditions, please complete "
    "your check-in using this secure link (location only when you allow it):\n\n"
    "{url}\n\n"
    "Questions? Call (239) 332-2245. ☘️"
)

TRACCAR_INSTALL_MESSAGE_TEMPLATE = (
    "Hi {name} — Shamrock Bail Bonds. Your bond requires GPS monitoring via our "
    "tracking app (you install it — nothing hidden).\n\n"
    "1) Install Traccar Client (free)\n"
    "2) Server: {server_url}\n"
    "3) Device ID: {device_id}\n"
    "4) Frequency: 60 seconds · allow location\n\n"
    "Also complete check-ins here: {portal_url}\n\n"
    "Questions? (239) 332-2245 ☘️"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _public_url() -> str:
    return os.getenv(
        "DASHBOARD_PUBLIC_URL",
        "https://leads.shamrockbailbonds.biz",
    ).rstrip("/")


async def provision_traccar_device(
    booking_number: str,
    *,
    defendant_name: str = "",
    county: str = "",
    phone: str = "",
    continuous: bool = False,
    actor: str = "system",
) -> dict:
    """
    Ensure a Traccar device exists for this bond and bind it in geo_devices.

    continuous=False: device receives portal check-in pings (OsmAnd inject)
    continuous=True: staff will walk defendant through Traccar Client install
    """
    from dashboard.services.traccar_client import (
        booking_to_unique_id,
        get_traccar_client,
        TraccarClient,
    )
    from dashboard.services.geo_intelligence import GeoIntelligenceService

    unique_id = booking_to_unique_id(booking_number)
    label = (defendant_name or booking_number).strip()
    name = f"{label} — {county or 'FL'}"[:120]

    health = {"status": "unknown"}
    try:
        tc = get_traccar_client()
        health = await tc.health_check()
        if health.get("status") not in ("online",):
            return {
                "success": False,
                "error": f"Traccar not ready: {health.get('status') or health.get('error')}",
                "unique_id": unique_id,
                "traccar_health": health,
            }
        device = await tc.ensure_device(
            name=name,
            unique_id=unique_id,
            category="person",
            phone=phone or "",
            attributes={
                "booking_number": booking_number,
                "county": county or "",
                "shamrock_device_type": "phone_app",
                "continuous": continuous,
                "provisioned_by": actor,
            },
        )
        traccar_id = device.get("id")
    except Exception as e:
        logger.warning("[checkin_enroll] Traccar provision failed booking=%s: %s", booking_number, e)
        return {
            "success": False,
            "error": str(e),
            "unique_id": unique_id,
            "traccar_health": health,
        }

    # Bind in Mongo if not already
    svc = GeoIntelligenceService()
    existing = await svc.list_devices(booking_number)
    bound = None
    for d in existing:
        if d.get("unique_id") == unique_id or d.get("traccar_device_id") == traccar_id:
            bound = d
            break
    if not bound:
        bound = await svc.register_device(
            booking_number=booking_number,
            county=county or "",
            device_type="phone_app",
            traccar_device_id=int(traccar_id),
            unique_id=unique_id,
            label=label,
            phone=phone or "",
        )

    setup = TraccarClient.client_setup_instructions(unique_id)

    active_bonds = get_collection("active_bonds")
    now = _now()
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "traccar_device_id": traccar_id,
            "traccar_unique_id": unique_id,
            "traccar_continuous": bool(continuous),
            "traccar_provisioned_at": now.isoformat(),
            "gps_engine": "traccar",
            "updated_at": now,
        }},
    )

    return {
        "success": True,
        "traccar_device_id": traccar_id,
        "unique_id": unique_id,
        "device": bound,
        "setup": setup,
        "continuous": continuous,
        "traccar_health": health,
    }


async def enable_checkin_monitoring(
    booking_number: str,
    *,
    frequency_days: int = DEFAULT_FREQUENCY_DAYS,
    source: str = "system",
    actor: str = "system",
    create_staff_task: bool = True,
    force_new_token: bool = False,
    provision_traccar: bool = True,
    continuous_gps: bool = False,
) -> dict:
    """
    Enable transparent check-in monitoring on an active bond.

    - Sets check_in_required, frequency, next_checkin_due
    - Generates defendant portal token if needed
    - Provisions Traccar device (in-stack GPS) so check-ins hit live map
    - Creates staff task to send enrollment link (no client auto-message)

    Returns portal URL + bond flags. Does NOT send SMS/iMessage.
    """
    booking_number = (booking_number or "").strip()
    if not booking_number:
        return {"success": False, "error": "booking_number is required"}

    active_bonds = get_collection("active_bonds")
    bond = await active_bonds.find_one({"booking_number": booking_number})
    if not bond:
        # Fallback: some flows only have bond_cases
        bond_cases = get_collection("bond_cases")
        case = await bond_cases.find_one({"booking_number": booking_number})
        if not case:
            return {"success": False, "error": "Bond not found"}
        # Soft-create minimal active_bonds shell for check-in only if case exists
        bond = {
            "booking_number": booking_number,
            "defendant_name": case.get("defendant_name") or case.get("Defendant_Name", ""),
            "status": case.get("status") or "active",
            "county": case.get("county", ""),
            "case_number": case.get("case_number") or case.get("Case_Number", ""),
        }
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$setOnInsert": {**bond, "created_at": _now()}},
            upsert=True,
        )
        bond = await active_bonds.find_one({"booking_number": booking_number})

    freq = max(1, int(frequency_days or DEFAULT_FREQUENCY_DAYS))
    now = _now()
    next_due = now + timedelta(days=freq)

    update_fields = {
        "check_in_required": True,
        "check_in_frequency_days": freq,
        "checkin_consent_version": CONSENT_VERSION,
        "checkin_enabled_at": now.isoformat(),
        "checkin_enabled_by": actor,
        "checkin_enabled_source": source,
        "monitoring_condition_summary": CONDITION_SUMMARY,
        "gps_engine": "traccar",
        "updated_at": now,
    }
    # Only set next due if missing or already overdue without a recent check-in
    if not bond.get("next_checkin_due") and not bond.get("next_check_in_due"):
        update_fields["next_checkin_due"] = next_due
        update_fields["next_check_in_due"] = next_due.isoformat()
    elif not bond.get("last_checkin") and not bond.get("last_check_in"):
        update_fields["next_checkin_due"] = next_due
        update_fields["next_check_in_due"] = next_due.isoformat()

    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": update_fields},
    )

    # Portal token (defendant)
    portal_url = bond.get("checkin_portal_url") or ""
    token = bond.get("checkin_portal_token") or ""
    if force_new_token or not portal_url:
        from dashboard.services.client_portal_service import generate_portal_token

        token_result = await generate_portal_token(
            booking_number=booking_number,
            role="defendant",
            created_by=f"checkin_enroll:{actor}",
        )
        if token_result.get("success"):
            portal_url = token_result["url"]
            token = token_result["token"]
            await active_bonds.update_one(
                {"booking_number": booking_number},
                {"$set": {
                    "checkin_portal_url": portal_url,
                    "checkin_portal_token": token,
                    "checkin_portal_generated_at": _now_iso(),
                }},
            )
        else:
            logger.warning(
                "[checkin_enroll] portal token failed booking=%s err=%s",
                booking_number, token_result.get("error"),
            )

    # Traccar device (in-stack GPS — not external vendor)
    traccar_info: dict = {"success": False, "skipped": True}
    if provision_traccar:
        traccar_info = await provision_traccar_device(
            booking_number,
            defendant_name=bond.get("defendant_name") or "",
            county=bond.get("county") or "",
            phone=bond.get("defendant_phone") or bond.get("phone") or "",
            continuous=continuous_gps,
            actor=actor,
        )
        if not traccar_info.get("success"):
            logger.warning(
                "[checkin_enroll] Traccar optional fail booking=%s: %s",
                booking_number, traccar_info.get("error"),
            )

    task_id = None
    if create_staff_task:
        try:
            from dashboard.services.task_engine import TaskEngine

            defendant = bond.get("defendant_name") or "defendant"
            setup_note = ""
            if traccar_info.get("success") and traccar_info.get("setup"):
                setup_note = (
                    f" Traccar ID: {traccar_info.get('unique_id')}. "
                    f"Continuous app install when elevated: "
                    f"{traccar_info['setup'].get('server_url')}."
                )
            task_id = await TaskEngine.create_task(
                booking_number=booking_number,
                title="Send check-in enrollment link",
                description=(
                    f"Bond signed / monitoring enabled. Send transparent check-in link "
                    f"to defendant ({defendant}). Portal: {portal_url or 'generate via enable-checkin'}."
                    f"{setup_note} GPS engine: Traccar (in-stack). "
                    f"Policy: monitoring-checkin-policy.md"
                ),
                due_date=now,
                task_type="checkin_enroll",
            )
            if continuous_gps and traccar_info.get("success"):
                await TaskEngine.create_task(
                    booking_number=booking_number,
                    title="Install Traccar Client (continuous GPS)",
                    description=(
                        f"Walk defendant through Traccar Client install. "
                        f"Device ID: {traccar_info.get('unique_id')}. "
                        f"{(traccar_info.get('setup') or {}).get('instructions', '')}"
                    ),
                    due_date=now,
                    task_type="traccar_install",
                )
        except Exception as e:
            logger.warning("[checkin_enroll] task create failed booking=%s: %s", booking_number, e)

    # Audit (no phone)
    try:
        audit = get_collection("audit_events")
        await audit.insert_one({
            "event_type": "checkin_monitoring_enabled",
            "entity_type": "bond_case",
            "entity_id": booking_number,
            "timestamp": now,
            "actor": actor,
            "source": source,
            "details": {
                "frequency_days": freq,
                "has_portal_url": bool(portal_url),
                "task_id": task_id,
                "consent_version": CONSENT_VERSION,
                "traccar_provisioned": bool(traccar_info.get("success")),
                "traccar_unique_id": traccar_info.get("unique_id"),
                "continuous_gps": continuous_gps,
                "gps_engine": "traccar",
            },
        })
    except Exception:
        pass

    logger.info(
        "[checkin_enroll] enabled booking=%s source=%s freq=%sd portal=%s traccar=%s",
        booking_number, source, freq, bool(portal_url), bool(traccar_info.get("success")),
    )

    return {
        "success": True,
        "booking_number": booking_number,
        "check_in_required": True,
        "check_in_frequency_days": freq,
        "next_checkin_due": (
            update_fields.get("next_check_in_due")
            or (bond.get("next_check_in_due") or bond.get("next_checkin_due"))
        ),
        "portal_url": portal_url,
        "task_id": task_id,
        "condition_summary": CONDITION_SUMMARY,
        "consent_version": CONSENT_VERSION,
        "gps_engine": "traccar",
        "traccar": traccar_info if not traccar_info.get("skipped") else {"skipped": True},
    }


async def send_checkin_link(
    booking_number: str,
    *,
    phone: str,
    actor: str = "staff",
    channel: str = "imessage",
) -> dict:
    """
    Human-gated delivery of defendant check-in portal link.

    Requires phone from staff (validated defendant phone). Never invents a number.
    """
    booking_number = (booking_number or "").strip()
    phone = (phone or "").strip()
    if not booking_number or not phone:
        return {"success": False, "error": "booking_number and phone are required"}

    # Ensure monitoring + portal URL exist
    enroll = await enable_checkin_monitoring(
        booking_number,
        source="staff_send",
        actor=actor,
        create_staff_task=False,
        force_new_token=False,
    )
    if not enroll.get("success"):
        return enroll

    portal_url = enroll.get("portal_url") or ""
    if not portal_url:
        enroll = await enable_checkin_monitoring(
            booking_number,
            source="staff_send",
            actor=actor,
            create_staff_task=False,
            force_new_token=True,
        )
        portal_url = enroll.get("portal_url") or ""
    if not portal_url:
        return {"success": False, "error": "Could not generate portal URL"}

    active_bonds = get_collection("active_bonds")
    bond = await active_bonds.find_one(
        {"booking_number": booking_number},
        {"defendant_name": 1},
    )
    name = (bond or {}).get("defendant_name") or "there"
    # First name only for message friendliness
    first = name.split()[0] if name and name != "there" else name

    msg = CHECKIN_MESSAGE_TEMPLATE.format(name=first, url=portal_url)

    send_result: dict = {"success": False, "error": "No messaging channel available"}
    try:
        from dashboard.services.bb_client import send_message_universal
        send_result = await send_message_universal(phone, msg)
        if send_result.get("success"):
            send_result["channel"] = send_result.get("channel") or "imessage"
    except Exception as e:
        logger.warning("[checkin_enroll] iMessage send failed: %s", e)
        try:
            from dashboard.services.twilio_service import TwilioService
            twilio = TwilioService()
            sms_result = twilio.send_sms(phone, msg)
            send_result = {"success": True, "channel": "sms", "sid": str(sms_result)}
        except Exception as sms_err:
            logger.warning("[checkin_enroll] SMS fallback failed: %s", sms_err)
            send_result = {"success": False, "error": str(sms_err)}

    now = _now()
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "checkin_link_last_sent_at": now.isoformat(),
            "checkin_link_last_sent_by": actor,
            "updated_at": now,
        }},
    )

    try:
        audit = get_collection("audit_events")
        await audit.insert_one({
            "event_type": "checkin_link_sent",
            "entity_type": "bond_case",
            "entity_id": booking_number,
            "timestamp": now,
            "actor": actor,
            "source": "staff_send_checkin_link",
            "details": {
                "channel": send_result.get("channel"),
                "success": bool(send_result.get("success")),
                # never store full phone
                "phone_last4": phone[-4:] if len(phone) >= 4 else "",
            },
        })
    except Exception:
        pass

    # Complete enroll task if open
    if send_result.get("success"):
        try:
            tasks = get_collection("tasks")
            await tasks.update_many(
                {
                    "booking_number": booking_number,
                    "task_type": "checkin_enroll",
                    "status": "pending",
                },
                {"$set": {
                    "status": "completed",
                    "completed_at": now.isoformat(),
                    "completed_by": actor,
                    "completion_note": "Check-in link sent",
                }},
            )
        except Exception:
            pass

    return {
        "success": bool(send_result.get("success")),
        "booking_number": booking_number,
        "portal_url": portal_url,
        "message_sent": bool(send_result.get("success")),
        "channel": send_result.get("channel", "unknown"),
        "error": send_result.get("error"),
    }


def get_condition_language() -> dict:
    """Return condition copy for CRM / paperwork UI."""
    return {
        "consent_version": CONSENT_VERSION,
        "summary": CONDITION_SUMMARY,
        "full_clause": (
            "Defendant Check-In & Location Verification. As a condition of this bail bond, "
            "the Defendant agrees to complete scheduled check-ins with Shamrock Bail Bonds "
            "using the secure link provided by Shamrock. Each check-in may require: "
            "(1) confirmation of current contact information and residence; "
            "(2) voluntary disclosure of approximate location via the device's location "
            "services only when the Defendant taps Check In and grants permission; and "
            "(3) optional photo verification when requested. For elevated risk, the Defendant "
            "may be required to install Shamrock's designated tracking application "
            "(Traccar Client / OsmAnd protocol) which reports location to Shamrock's "
            "in-house GPS server — with the Defendant's knowledge; this is not covert "
            "spyware. Failure to complete required check-ins or install required monitoring "
            "may result in increased supervision, bond surrender proceedings, or other "
            "remedies available under the bond and applicable law. Location data is used "
            "solely for bond compliance and risk management and is not sold to third parties."
        ),
        "indemnitor_notice": (
            "The bond includes defendant check-in requirements and may include GPS monitoring "
            "via Shamrock's Traccar system. Shamrock will contact the defendant for compliance "
            "check-ins. Missed check-ins may increase risk of forfeiture and may require "
            "indemnitor cooperation."
        ),
        "message_template": CHECKIN_MESSAGE_TEMPLATE,
        "traccar_install_template": TRACCAR_INSTALL_MESSAGE_TEMPLATE,
        "gps_engine": "traccar",
    }


async def push_checkin_to_traccar(
    booking_number: str,
    lat: float,
    lng: float,
    *,
    accuracy: Optional[float] = None,
) -> dict:
    """
    After a consented portal check-in, inject position into Traccar so Tracking/live map updates.
    Provisions device if missing. Fail-soft if Traccar is down.
    """
    from dashboard.services.traccar_client import (
        booking_to_unique_id,
        get_traccar_client,
    )
    from dashboard.services.geo_intelligence import GeoIntelligenceService

    unique_id = booking_to_unique_id(booking_number)
    active_bonds = get_collection("active_bonds")
    bond = await active_bonds.find_one(
        {"booking_number": booking_number},
        {"defendant_name": 1, "county": 1, "traccar_device_id": 1, "traccar_unique_id": 1},
    ) or {}

    try:
        tc = get_traccar_client()
        if not bond.get("traccar_device_id"):
            prov = await provision_traccar_device(
                booking_number,
                defendant_name=bond.get("defendant_name") or "",
                county=bond.get("county") or "",
                continuous=False,
                actor="portal_checkin",
            )
            if not prov.get("success"):
                return {"success": False, "error": prov.get("error"), "unique_id": unique_id}
            unique_id = prov.get("unique_id") or unique_id
            traccar_id = prov.get("traccar_device_id")
        else:
            unique_id = bond.get("traccar_unique_id") or unique_id
            traccar_id = bond.get("traccar_device_id")

        inject = await tc.report_osmand_position(
            unique_id, lat, lng, accuracy=accuracy, timestamp=_now(),
        )

        # Also sync via geo service so location_history is consistent if webhook is slow
        if traccar_id:
            try:
                svc = GeoIntelligenceService()
                await svc.sync_position(
                    traccar_device_id=int(traccar_id),
                    lat=float(lat),
                    lng=float(lng),
                    accuracy=float(accuracy or 0),
                    timestamp=_now_iso(),
                    attributes={"source": "portal_checkin"},
                )
            except Exception as se:
                logger.debug("[checkin_enroll] geo sync after portal: %s", se)

        return {
            "success": bool(inject.get("success")),
            "unique_id": unique_id,
            "traccar_device_id": traccar_id,
            "inject": inject,
        }
    except Exception as e:
        logger.warning("[checkin_enroll] push_checkin_to_traccar: %s", e)
        return {"success": False, "error": str(e), "unique_id": unique_id}
