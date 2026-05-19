"""
FTA Surrender Workflow Service — ShamrockLeads
===============================================
When an FTA alert reaches Level 3, this service:
  1. Assembles a surrender packet (defendant info, bond details, court info)
  2. Sends a SignNow surrender authorization document to the agent for signature
  3. Sends a BB iMessage to the indemnitor notifying them of the surrender action
  4. Logs the surrender initiation in the fta_alerts collection
  5. Emits a bond_surrender_initiated SSE event

The surrender document template ID is configured via SIGNNOW_SURRENDER_TEMPLATE_ID
in the environment. If not set, the service skips SignNow and logs a warning.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from dashboard.extensions import get_collection, get_db

log = logging.getLogger("shamrock.fta_surrender")

_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://shamrockbailbonds.biz")
_AGENT_EMAIL = os.getenv("AGENT_EMAIL", "admin@shamrockbailbonds.biz")
_AGENT_PHONE = os.getenv("AGENT_PHONE", "2393322245")
_SURRENDER_TEMPLATE_ID = os.getenv("SIGNNOW_SURRENDER_TEMPLATE_ID", "")


class FTASurrenderService:
    """Orchestrates the Level 3 FTA → surrender workflow."""

    def __init__(self, db=None):
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────
    async def initiate_surrender(
        self,
        booking_number: str,
        initiated_by: str = "system",
        notes: str = "",
    ) -> dict:
        """
        Initiate the surrender workflow for a Level 3 FTA.

        Returns a dict with:
          success, booking_number, signnow_sent, bb_sent, surrender_id
        """
        active_bonds = get_collection("active_bonds")
        fta_col = get_collection("fta_alerts")

        # 1. Load bond
        bond = await active_bonds.find_one(
            {"booking_number": booking_number},
            {"_id": 0},
        )
        if not bond:
            return {"success": False, "error": f"Bond {booking_number} not found"}

        # 2. Check FTA record
        fta = await fta_col.find_one(
            {"booking_number": booking_number, "resolved": False},
            sort=[("escalation_level", -1)],
        )
        if not fta:
            return {"success": False, "error": "No open FTA alert found for this booking"}

        if fta.get("escalation_level", 1) < 3:
            return {
                "success": False,
                "error": f"FTA is only Level {fta.get('escalation_level', 1)} — surrender requires Level 3",
            }

        if fta.get("surrender_initiated"):
            return {"success": False, "error": "Surrender already initiated for this FTA"}

        # 3. Generate a surrender ID
        surrender_id = f"SRR-{booking_number}-{secrets.token_hex(4).upper()}"

        # 4. Send SignNow surrender authorization document
        signnow_sent = False
        signnow_doc_id = None
        if _SURRENDER_TEMPLATE_ID:
            try:
                signnow_sent, signnow_doc_id = await self._send_signnow_surrender(
                    bond, surrender_id
                )
            except Exception as e:
                log.warning("[Surrender] SignNow send failed for %s: %s", booking_number, e)
        else:
            log.warning(
                "[Surrender] SIGNNOW_SURRENDER_TEMPLATE_ID not set — skipping SignNow for %s",
                booking_number,
            )

        # 5. Notify indemnitor via BB iMessage
        bb_sent = False
        try:
            bb_sent = await self._notify_indemnitor_bb(bond, surrender_id)
        except Exception as e:
            log.warning("[Surrender] BB notify failed for %s: %s", booking_number, e)

        # 6. Notify agent via BB
        try:
            await self._notify_agent_bb(bond, surrender_id)
        except Exception as e:
            log.warning("[Surrender] Agent BB notify failed for %s: %s", booking_number, e)

        # 7. Update FTA record
        now = datetime.now(timezone.utc).isoformat()
        await fta_col.update_one(
            {"booking_number": booking_number, "resolved": False},
            {"$set": {
                "surrender_initiated": True,
                "surrender_id": surrender_id,
                "surrender_initiated_at": now,
                "surrender_initiated_by": initiated_by,
                "surrender_notes": notes,
                "signnow_doc_id": signnow_doc_id,
            }},
        )

        # 8. Update bond record
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "surrender_initiated": True,
                "surrender_id": surrender_id,
                "surrender_initiated_at": now,
                "status": "surrender_pending",
            }},
        )

        # 9. Emit SSE event
        try:
            from dashboard.routers.events import emit_event
            await emit_event("bond_surrender_initiated", {
                "booking_number": booking_number,
                "defendant_name": bond.get("defendant_name", ""),
                "surrender_id": surrender_id,
                "signnow_sent": signnow_sent,
            })
        except Exception:
            pass

        log.warning(
            "[Surrender] INITIATED — booking=%s surrender_id=%s signnow=%s bb=%s by=%s",
            booking_number, surrender_id, signnow_sent, bb_sent, initiated_by,
        )

        return {
            "success": True,
            "booking_number": booking_number,
            "surrender_id": surrender_id,
            "signnow_sent": signnow_sent,
            "signnow_doc_id": signnow_doc_id,
            "bb_sent": bb_sent,
            "initiated_by": initiated_by,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # SignNow surrender authorization document
    # ─────────────────────────────────────────────────────────────────────────
    async def _send_signnow_surrender(self, bond: dict, surrender_id: str):
        """Copy the surrender template, prefill fields, and send to agent."""
        import httpx
        from dashboard.services.signnow_service import SignNowService

        svc = SignNowService(api_token=os.environ.get("SIGNNOW_API_TOKEN", ""))
        token = await svc.get_token()

        defendant_name = bond.get("defendant_name", "")
        booking_number = bond.get("booking_number", "")
        bond_amount = bond.get("bond_amount", 0)
        court_date = bond.get("court_date", "")
        county = bond.get("county", "")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        doc_name = f"Surrender Auth — {defendant_name} — {surrender_id}"

        async with httpx.AsyncClient(timeout=30) as client:
            # Copy template
            copy_resp = await client.post(
                f"{svc.base_url}/template/{_SURRENDER_TEMPLATE_ID}/copy",
                headers=headers,
                json={"document_name": doc_name},
            )
            copy_resp.raise_for_status()
            doc_id = copy_resp.json().get("id", "")

            if not doc_id:
                raise ValueError("SignNow template copy returned no document ID")

            # Prefill fields
            fields = [
                {"external_id": "defendant_name",  "value": defendant_name},
                {"external_id": "booking_number",  "value": booking_number},
                {"external_id": "bond_amount",     "value": f"${bond_amount:,.0f}"},
                {"external_id": "court_date",      "value": court_date},
                {"external_id": "county",          "value": county},
                {"external_id": "surrender_id",    "value": surrender_id},
                {"external_id": "initiated_date",  "value": datetime.now(timezone.utc).strftime("%m/%d/%Y")},
            ]
            await client.put(
                f"{svc.base_url}/document/{doc_id}",
                headers=headers,
                json={"fields": fields},
            )

            # Send invite to agent
            await client.post(
                f"{svc.base_url}/document/{doc_id}/invite",
                headers=headers,
                json={
                    "to": [{"email": _AGENT_EMAIL, "role": "Agent", "order": 1}],
                    "from": _AGENT_EMAIL,
                    "subject": f"⚠️ Surrender Authorization Required — {defendant_name}",
                    "message": (
                        f"A Level 3 FTA has been detected for {defendant_name} "
                        f"(Booking #{booking_number}). Please review and sign the "
                        f"surrender authorization to proceed.\n\nSurrender ID: {surrender_id}"
                    ),
                },
            )

        log.info("[Surrender] SignNow doc %s sent to agent for %s", doc_id, booking_number)
        return True, doc_id

    # ─────────────────────────────────────────────────────────────────────────
    # BB iMessage — indemnitor notification
    # ─────────────────────────────────────────────────────────────────────────
    async def _notify_indemnitor_bb(self, bond: dict, surrender_id: str) -> bool:
        """Send a BB iMessage to the indemnitor notifying them of surrender action."""
        phone = (
            bond.get("indemnitor_phone")
            or bond.get("indemnitor_cell")
            or bond.get("indemnitor_phone_1")
            or ""
        )
        if not phone:
            log.warning("[Surrender] No indemnitor phone for %s", bond.get("booking_number"))
            return False

        defendant = bond.get("defendant_name", "the defendant")
        first_name = bond.get("indemnitor_name", "").split()[0] if bond.get("indemnitor_name") else "Cosigner"

        # Geo link
        geo_token = secrets.token_urlsafe(12)
        geo_url = f"{_PUBLIC_URL.rstrip('/')}/g/{geo_token}"
        try:
            geo_col = get_collection("geo_pings")
            await geo_col.insert_one({
                "token": geo_token,
                "booking_number": bond.get("booking_number"),
                "phone": phone,
                "recipient": "indemnitor_surrender_notice",
                "status": "pending",
                "ping_count": 0,
                "pings": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            geo_url = ""

        geo_line = f"\n\nConfirm your location: {geo_url}" if geo_url else ""

        msg = (
            f"URGENT — {first_name}, this is Shamrock Bail Bonds. "
            f"We have been unable to locate {defendant} and a Failure to Appear has been confirmed. "
            f"We are initiating the surrender process (Ref: {surrender_id}). "
            f"You must contact us immediately at (239) 332-2245 or your bond liability may increase significantly."
            f"{geo_line}\n\n☘️ Shamrock Bail Bonds (239) 332-2245"
        )

        from dashboard.services.bb_client import send_message_universal
        result = await send_message_universal(phone, msg)
        return result.get("success", False)

    # ─────────────────────────────────────────────────────────────────────────
    # BB iMessage — agent notification
    # ─────────────────────────────────────────────────────────────────────────
    async def _notify_agent_bb(self, bond: dict, surrender_id: str) -> bool:
        """Send a BB iMessage to the agent phone with surrender details."""
        if not _AGENT_PHONE:
            return False

        defendant = bond.get("defendant_name", "Unknown")
        booking = bond.get("booking_number", "")
        bond_amount = bond.get("bond_amount", 0)
        county = bond.get("county", "")

        msg = (
            f"🆘 SURRENDER INITIATED\n"
            f"Defendant: {defendant}\n"
            f"Booking: {booking}\n"
            f"Bond: ${bond_amount:,.0f} — {county}\n"
            f"Ref: {surrender_id}\n\n"
            f"SignNow authorization has been sent to your email. "
            f"Check the FTA Alerts tab for full details."
        )

        from dashboard.services.bb_client import send_message_universal
        result = await send_message_universal(_AGENT_PHONE, msg)
        return result.get("success", False)
