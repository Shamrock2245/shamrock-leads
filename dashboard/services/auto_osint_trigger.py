"""
ShamrockLeads — Automatic OSINT Profiling Trigger
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Automatically launches an OSINT digital footprint scan when a bond
is activated or monitored. Gathers linked platform accounts (Amazon,
eBay, PayPal, CashApp, Apple, social networks) to mitigate risk in
case a defendant switches SIM cards or abandons their device.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from dashboard.extensions import get_collection
from dashboard.models.osint import OSINTScanRequest, SubjectType, EngineType

logger = logging.getLogger(__name__)


async def trigger_auto_osint_profiling(
    bond_data: Dict[str, Any],
    actor: str = "auto_trigger",
    force: bool = False,
) -> Optional[str]:
    """
    Evaluate a bond document and initiate an automated OSINT profiling scan.
    
    Safe & Non-blocking:
    - Verifies subject has at least one digital anchor (email, phone, plate).
    - Deduplicates: will not re-scan if scanned in the last 30 days (unless force=True).
    - Runs in background task and attaches discovered accounts to the bond dossier.
    """
    booking_number = (
        bond_data.get("booking_number")
        or bond_data.get("booking")
        or ""
    ).strip()

    if not booking_number:
        logger.debug("[auto_osint] No booking_number provided — skipping")
        return None

    email = (
        bond_data.get("defendant_email")
        or bond_data.get("email")
        or ""
    ).strip().lower()

    phone = (
        bond_data.get("defendant_phone")
        or bond_data.get("phone")
        or ""
    ).strip()

    plate = (
        bond_data.get("vehicle_plate")
        or bond_data.get("license_plate")
        or bond_data.get("plate")
        or ""
    ).strip().upper()

    full_name = (
        bond_data.get("defendant_name")
        or bond_data.get("name")
        or ""
    ).strip()

    if not email and not phone and not plate:
        logger.debug("[auto_osint] No email, phone, or plate for %s — skipping", booking_number)
        return None

    # Deduplication: Check if scanned recently
    if not force:
        try:
            active_bonds = get_collection("active_bonds")
            existing = await active_bonds.find_one(
                {"booking_number": booking_number},
                {"osint_last_scanned_at": 1, "osint_intel": 1}
            )
            if existing and existing.get("osint_last_scanned_at"):
                last_scanned = existing["osint_last_scanned_at"]
                if isinstance(last_scanned, str):
                    try:
                        last_scanned = datetime.fromisoformat(last_scanned.replace("Z", "+00:00"))
                    except Exception:
                        last_scanned = None
                if last_scanned and datetime.now(timezone.utc) - last_scanned < timedelta(days=30):
                    logger.info("[auto_osint] %s already scanned on %s — skipping duplicate", booking_number, last_scanned)
                    return None
        except Exception as err:
            logger.warning("[auto_osint] Dedup lookup warning for %s: %s", booking_number, err)

    # Assemble engines
    engines = [EngineType.maigret, EngineType.sherlock]
    if email and "@" in email:
        engines.append(EngineType.holehe)
        engines.append(EngineType.h8mail)
    if phone:
        engines.append(EngineType.ignorant)
    if plate:
        engines.append(EngineType.hibf)

    # Spawn background task
    asyncio.create_task(
        _execute_auto_osint(
            booking_number=booking_number,
            full_name=full_name,
            email=email,
            phone=phone,
            plate=plate,
            engines=engines,
            actor=actor,
        )
    )
    logger.info("[auto_osint] Dispatched OSINT profiling for %s (engines=%s)", booking_number, [e.value for e in engines])
    return f"dispatched_{booking_number}"


async def _execute_auto_osint(
    booking_number: str,
    full_name: str,
    email: str,
    phone: str,
    plate: str,
    engines: list[EngineType],
    actor: str,
) -> None:
    """Execute the scan via OSINTService and attach findings to the subject."""
    try:
        from dashboard.services.osint_service import get_osint_service
        osint_svc = get_osint_service()

        req = OSINTScanRequest(
            subject_type=SubjectType.defendant,
            subject_id=booking_number,
            full_name=full_name or None,
            email=email or None,
            phone=phone or None,
            license_plate=plate or None,
            engines=engines,
            second_opinion=False,
        )

        scan_id = await osint_svc.run_scan(req, actor=actor)
        logger.info("[auto_osint] Started scan %s for %s", scan_id, booking_number)

        # Poll scan completion with timeout (max 3 minutes)
        max_wait = 180
        waited = 0
        while waited < max_wait:
            await asyncio.sleep(5)
            waited += 5
            scan_doc = await osint_svc.get_scan(scan_id)
            if not scan_doc:
                break
            status = scan_doc.get("status")
            if status in ("completed", "partial", "failed"):
                break

        # Attach findings directly to active_bonds record
        attached = await osint_svc.attach_to_subject(scan_id, actor=actor)
        if attached:
            logger.info("[auto_osint] Successfully attached OSINT profile to bond %s", booking_number)
    except Exception as exc:
        logger.error("[auto_osint] Background profiling error for %s: %s", booking_number, exc)
