"""Contacts API — OSINT contact discovery for defendants."""
from __future__ import annotations

import hashlib
import logging

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection, get_db
from dashboard.services.contact_discovery import (
    CONTACTS_REV_FIELD,
    CandidateReviewError,
    ContactDiscoveryService,
    ContactsLegacyAmbiguous,
    canonical_place,
    contact_identity,
    find_contact_doc,
    review_candidate,
)

logger = logging.getLogger(__name__)

contacts_bp = APIRouter(prefix="/api", tags=["contacts"])


@contacts_bp.post("/contacts/discover")
async def discover_contacts(request: Request):
    """Run OSINT contact discovery for a defendant."""
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number")
        full_name = data.get("full_name")
        county = data.get("county")
        state = data.get("state")
        address = data.get("address")

        if not booking_number:
            return JSONResponse(
                {"error": "booking_number_required"},
                status_code=400,
            )

        service = ContactDiscoveryService(get_db())
        result = await service.discover(
            booking_number=booking_number,
            full_name=full_name,
            county=county,
            state=state,
            address=address,
        )
        if result.get("discovery_status") == "identity_ambiguous":
            return JSONResponse(
                {"error": "county_and_state_required", "discovery_status": "identity_ambiguous"},
                status_code=409,
            )
        if result.get("discovery_status") == ContactsLegacyAmbiguous.code:
            # Several legacy booking-only docs could be this arrest's: nothing
            # was written. Needs a human; retrying will not resolve it.
            return JSONResponse(
                {"error": "legacy_contacts_ambiguous", "discovery_status": ContactsLegacyAmbiguous.code},
                status_code=409,
            )
        return result
    except Exception as exc:
        logger.warning("contact discover failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)


@contacts_bp.get("/contacts/{booking_number}")
async def get_contacts(booking_number: str, county: Optional[str] = None, state: Optional[str] = None):
    """Get discovered contacts for one arrest (state + county + booking).

    Without county/state this answers only when exactly one stored doc has
    the booking number; two counties with the same booking return 409.
    """
    empty = {
        "contacts": [],
        "candidates": [],
        "discovered_contacts": [],
        "discovery_status": "not_found",
        "status": "not_found",
    }
    try:
        contacts_col = get_collection("contacts")
        identity = contact_identity(state, county, booking_number)
        if identity:
            # Same lookup as the refresh write: the full identity first, then a
            # single legacy booking-only doc when exactly one arrest has the
            # booking. Any doubt is a 409; a GET never writes.
            try:
                doc = await find_contact_doc(contacts_col, identity, arrests=get_collection("arrests"))
            except ContactsLegacyAmbiguous:
                return JSONResponse(
                    {**empty, "error": "legacy_contacts_ambiguous", "discovery_status": ContactsLegacyAmbiguous.code},
                    status_code=409,
                )
            if doc:
                doc = {key: value for key, value in doc.items() if key != "_id"}
        else:
            query = {"booking_number": booking_number}
            c_county, c_state = canonical_place(county, state)
            if county:
                query["county"] = c_county or str(county)
            if state:
                query["state"] = c_state or str(state).strip().upper()
            rows = await contacts_col.find(query, {"_id": 0}).to_list(length=2)
            if len(rows) > 1:
                return JSONResponse(
                    {**empty, "error": "county_and_state_required", "discovery_status": "identity_ambiguous"},
                    status_code=409,
                )
            doc = rows[0] if rows else None
        if not doc:
            return empty
        contacts = list(doc.get("discovered_contacts") or [])
        doc["contacts"] = contacts
        doc["candidates"] = contacts
        doc["discovery_status"] = doc.get("discovery_status") or "not_found"
        # Staff send this back with approve/reject (compare-and-swap).
        doc[CONTACTS_REV_FIELD] = int(doc.get(CONTACTS_REV_FIELD) or 0)
        doc.pop("review_log", None)
        return doc
    except Exception as exc:
        logger.warning("contact read failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "discovery_failed"}, status_code=500)


# Roles that may approve or reject a co-signer candidate. Sub-agents and the
# recovery desk may not; a request with no staff session (machine key, or the
# no-PIN development passthrough) may not either.
_REVIEW_ROLES = frozenset({"god_admin", "admin", "staff"})


def _staff_reviewer(request: Request) -> tuple[Optional[str], str, int]:
    """(actor, role, http_status). The actor is a pseudonymous staff session
    id (hash of the signed-in email), never the email itself."""
    sess: Any = getattr(request.state, "sl_session", None)
    if not isinstance(sess, dict) or not sess.get("auth"):
        return None, "", 401
    role = str(sess.get("role") or "")
    if role not in _REVIEW_ROLES:
        return None, role, 403
    email = str(sess.get("email") or "").strip().lower()
    if not email:
        return None, role, 401
    return "staff:" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:16], role, 200


@contacts_bp.post("/contacts/{booking_number}/candidates/{candidate_id}/review")
async def review_contact_candidate(booking_number: str, candidate_id: str, request: Request):
    """Approve or reject one discovered co-signer candidate (staff only).

    Body: ``{"county", "state", "decision": "approve"|"reject", "rev"}`` where
    ``rev`` is the ``discovery_rev`` the drawer read. A changed doc returns
    409 ``stale_revision`` and nothing is written. Nothing is sent.
    """
    actor, role, status = _staff_reviewer(request)
    if status != 200:
        error = "staff_session_required" if status == 401 else "role_not_allowed"
        return JSONResponse({"error": error}, status_code=status)
    try:
        data = await request.json() or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    identity = contact_identity(data.get("state"), data.get("county"), booking_number)
    if not identity:
        return JSONResponse({"error": "county_and_state_required"}, status_code=400)
    rev = data.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int):
        return JSONResponse({"error": "rev_required"}, status_code=400)
    try:
        return await review_candidate(
            get_db(),
            identity=identity,
            candidate_id=str(candidate_id or ""),
            decision=str(data.get("decision") or ""),
            expected_rev=rev,
            actor=actor or "",
            actor_role=role,
        )
    except CandidateReviewError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.http_status)
    except Exception as exc:
        logger.warning("contact review failed error_type=%s", type(exc).__name__)
        return JSONResponse({"error": "review_failed"}, status_code=500)
