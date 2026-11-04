"""Indemnitor self-pay: the portal's per-bond invoice link.

``POST /api/portal/{token}/invoice-link`` (contract: go-live prep §2.2).

* Indemnitor tokens only. Five requests a minute per token.
* The bond is resolved from the token's booking across ``bond_cases`` and
  ``active_bonds`` (``booking_number`` canonical, legacy ``Booking_Number``
  read-only alias, padded values, deduped by ``_id``). Two distinct cases
  fail closed with ``bond_ambiguous``.
* The amount is the confirmed due amount only (``invoice_due_cents``). The
  request body never carries an amount. With ``PREMIUM_AUTO_CONFIRM`` on, an
  unconfirmed bond gets the auto premium (paid in full at max($100, 10%)).
* One invoice per bond through ``create_locked_invoice`` and its claim.
* A URL is returned only when it is the bond's own stored invoice link, on
  the SwipeSimple origin, numbered with this booking, bound to this bond,
  and stored with the exact due cents. Never a static link: the portal
  shows "Call (239) 332-2245" instead.
* ``send_copy`` texts the link only through ``dispatch_invoice`` (its
  one-send claim) and only when ``SWIPESIMPLE_DISPATCH_LIVE`` is on.
* ``SWIPESIMPLE_LIVE`` off and no stored link: 503
  ``invoicing_unavailable``; nothing is created.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple
from urllib.parse import urlparse

from dashboard.services import swipesimple_invoice_service as ss
from dashboard.services.bond_booking_lookup import BondAmbiguous, resolve_bond_pair
from dashboard.services.payment_links import case_invoice_link
from dashboard.services.premium_confirmation_service import (
    PREMIUM_CONFIRMED_AMOUNT_FIELD,
    PREMIUM_CONFIRMED_BY_FIELD,
    PremiumConfirmationError,
    auto_confirm_enabled,
    auto_confirm_premium,
    cents_to_amount,
    confirmation_complete,
    invoice_due_cents,
    non_negative_cents,
)

logger = logging.getLogger(__name__)

CALL_PHONE = "(239) 332-2245"
CALL_TEL = "tel:+12393322245"

RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 60.0
_rate_lock = threading.Lock()
_rate_hits: Dict[str, Deque[float]] = {}


def _rate_key(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def rate_limited(token: str, now: Optional[float] = None) -> bool:
    """True when this token already used its five requests in the last minute.

    In-process sliding window (per worker). The token is hashed, never kept.
    """
    now = time.monotonic() if now is None else now
    key = _rate_key(token)
    with _rate_lock:
        hits = _rate_hits.setdefault(key, deque())
        while hits and now - hits[0] >= RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return True
        hits.append(now)
        if len(_rate_hits) > 10000:
            for k in [k for k, v in _rate_hits.items() if not v]:
                _rate_hits.pop(k, None)
        return False


def reset_rate_limits() -> None:
    with _rate_lock:
        _rate_hits.clear()


# ---------------------------------------------------------------------------
# Amount and link checks (read-only)
# ---------------------------------------------------------------------------

def confirmed_due_cents(case: Optional[dict], active: Optional[dict]) -> Optional[int]:
    """The confirmed amount due now, or None (unconfirmed, zero, or disagreeing)."""
    values = {invoice_due_cents(doc) for doc in (case, active) if confirmation_complete(doc)}
    if len(values) != 1:
        return None
    due = values.pop()
    return due if isinstance(due, int) and due > 0 else None


def confirmed_by(case: Optional[dict], active: Optional[dict]) -> str:
    """``"auto"`` or ``"staff"`` (never the staff member's name or email), else ``""``."""
    for doc in (case, active):
        if confirmation_complete(doc):
            who = str(doc.get(PREMIUM_CONFIRMED_BY_FIELD) or "").strip()
            return "auto" if who == "auto" else "staff"
    return ""


def is_paid(case: Optional[dict], active: Optional[dict]) -> bool:
    return any(isinstance(doc, dict) and ss._is_paid(doc) for doc in (case, active))


def _case_ids(*docs: Optional[dict]) -> set:
    ids = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for key in ("bond_case_id", "Bond_Case_ID", "bond_id"):
            val = str(doc.get(key) or "").strip()
            if val:
                ids.add(val)
        if doc.get("_id") is not None:
            ids.add(str(doc.get("_id")))
    return ids


def _is_static_link(url: str) -> bool:
    """SwipeSimple static (``/links/lnk_…``) pay pages are never a bond's invoice."""
    path = (urlparse(url).path or "").lower()
    return path.startswith("/links/") or "/lnk_" in path


def verified_invoice_link(
    case: Optional[dict],
    active: Optional[dict],
    booking: str,
    due_cents: Optional[int],
) -> Tuple[Optional[str], Optional[str]]:
    """``(url, None)`` for the bond's own proven invoice link, else ``(None, code)``.

    ``(None, None)`` means no link is stored. Codes: ``invoice_amount_unproven``,
    ``invoice_unbound``, ``invoice_origin``. Same checks as the co-signer
    page (``cosigner_link_service._payment_view``) plus: the bond id may be
    any id of this case, and a static pay page is refused.
    """
    from dashboard.services.cosigner_link_service import (
        _invoice_amount_unproven,
        _invoice_host_rejected,
    )

    booking = str(booking or "").strip()
    ids = _case_ids(case, active)
    for doc in (case, active):
        link = case_invoice_link(doc)
        if not link:
            continue
        if due_cents is None or _invoice_amount_unproven(doc, due_cents):
            return None, "invoice_amount_unproven"
        number = str(doc.get("swipesimple_invoice_number") or "").strip()
        stored_bond = str(doc.get("swipesimple_invoice_bond_id") or "").strip()
        if not booking or number != booking or not stored_bond or stored_bond not in ids:
            return None, "invoice_unbound"
        if not link.startswith("https://") or _invoice_host_rejected(link) or _is_static_link(link):
            return None, "invoice_origin"
        return link, None
    return None, None


async def portal_payment_view(booking: str) -> Dict[str, Any]:
    """What the indemnitor portal may show: confirmed due amount and own link only.

    Never raises; any doubt shows no link and the call-us fallback.
    """
    view: Dict[str, Any] = {
        "amount_due": None,
        "premium": None,
        "payment_link": None,
        "premium_is_confirmed": False,
        "paid": False,
        "invoice_request_allowed": False,
        "call_phone": CALL_PHONE,
    }
    try:
        case, active = await resolve_bond_pair(booking)
    except BondAmbiguous:
        view["error"] = "bond_ambiguous"
        return view
    except Exception as exc:
        logger.warning("[portal_invoice] status lookup failed err_type=%s", type(exc).__name__)
        view["error"] = "lookup_failed"
        return view
    if not case and not active:
        return view
    due = confirmed_due_cents(case, active)
    paid = is_paid(case, active)
    view["paid"] = paid
    if due is not None:
        view["premium_is_confirmed"] = True
        view["amount_due"] = cents_to_amount(due)
        for doc in (case, active):
            if confirmation_complete(doc):
                cents = non_negative_cents(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD))
                view["premium"] = cents_to_amount(cents) if cents is not None else None
                break
        if not paid:
            link, _err = verified_invoice_link(case, active, booking, due)
            view["payment_link"] = link
    view["invoice_request_allowed"] = bool(
        not paid and not view["payment_link"] and (due is not None or auto_confirm_enabled())
    )
    return view


# ---------------------------------------------------------------------------
# POST /api/portal/{token}/invoice-link
# ---------------------------------------------------------------------------

_SESSION_CODES = (
    "swipesimple_session_not_configured",
    "create_invoice_http_401",
    "create_invoice_http_403",
    "csrf_token_not_found_in_new_invoice_html",
)
_AMOUNT_CODES = (
    "premium_mismatch_vs_bondcase",
    "invoice_amount_unproven",
    "invoice_amount_mismatch",
    "premium_mismatch_across_collections",
)
_UNCONFIRMED_CODES = (
    "premium_missing_on_bondcase",
    "premium_not_numeric_on_bondcase",
    "premium_ambiguous_on_bondcase",
    "premium_not_exact_cents",
    "premium_zero",
    "premium_negative",
    "down_payment_zero",
)
_REVIEW_CODES = (
    "invoice_create_pending_id_resolution",
    "invoice_claim_stale_manual_review",
    "invoice_claim_index_unavailable",
    "share_invoice_missing_payment_link",
    "invoice_id_unresolved_after_create_302",
)


def _err(status: int, code: str, **extra: Any) -> Tuple[int, Dict[str, Any]]:
    return status, {"ok": False, "error": code, **extra}


def map_invoice_error(exc: BaseException) -> Tuple[int, Dict[str, Any]]:
    """SwipeSimple service error → contract status and code. No message text."""
    if isinstance(exc, ss.SwipeSimpleLiveDisabled):
        return _err(503, "invoicing_unavailable")
    code = ss._error_code(exc) if isinstance(exc, ss.SwipeSimpleInvoiceError) else ""
    if code in ("bond_not_found", "missing_bond_id"):
        return _err(404, "bond_not_found")
    if code == "bond_ambiguous":
        return _err(409, "bond_ambiguous")
    if code == "missing_booking_number":
        return _err(409, "missing_booking_number")
    if code == "staff_test_case_invoice_disabled":
        return _err(409, "staff_test_case_invoice_disabled")
    if code == "premium_estimate_unconfirmed":
        return _err(409, "premium_estimate_unconfirmed")
    if code in _UNCONFIRMED_CODES:
        return _err(409, "premium_unconfirmed")
    if code in _AMOUNT_CODES:
        return _err(409, "amount_mismatch")
    if code in _SESSION_CODES or code.startswith("swipesimple_session"):
        return _err(503, "session_expired")
    if code in _REVIEW_CODES or code.startswith("invoice_claim"):
        return _err(409, "needs_review")
    return _err(503, "invoicing_unavailable")


def _estimate_only(case: Optional[dict], active: Optional[dict]) -> bool:
    for doc in (case, active):
        if not isinstance(doc, dict):
            continue
        if doc.get("premium_is_estimate") is True:
            return True
        if str(doc.get("source") or "").strip() in ("intake_promotion",):
            return True
    return False


def _create_bond_id(case: Optional[dict], active: Optional[dict]) -> str:
    for doc in (case, active):
        if isinstance(doc, dict):
            for key in ("bond_case_id", "Bond_Case_ID"):
                val = str(doc.get(key) or "").strip()
                if val:
                    return val
    doc = case or active or {}
    return str(doc.get("_id") or "").strip()


async def _dispatch_copy(bond_id: str, channel: str, requested: bool) -> Dict[str, Any]:
    live = ss.dispatch_live_enabled()
    if not requested:
        return {"dispatched": False, "dry_run": not live, "requested": False}
    if not live:
        logger.info("[portal_invoice] send_copy dry-run bond_id=%s channel=%s", bond_id, channel)
        return {"dispatched": False, "dry_run": True, "requested": True}
    try:
        result = await ss.dispatch_invoice(bond_id, channel)  # one-send claim inside
    except Exception as exc:
        code = ss._error_code(exc)
        logger.warning("[portal_invoice] send_copy failed bond_id=%s error_code=%s", bond_id, code)
        return {"dispatched": False, "dry_run": False, "requested": True, "error": code}
    out = {
        "dispatched": bool(result.get("sent")),
        "dry_run": bool(result.get("dry_run")),
        "requested": True,
    }
    if result.get("already_dispatched"):
        out["already_dispatched"] = True
    if result.get("in_progress"):
        out["in_progress"] = True
    return out


async def request_invoice_link(token_data: Dict[str, Any], body: Any) -> Tuple[int, Dict[str, Any]]:
    """Create-or-return the indemnitor's invoice link. Returns ``(status, body)``."""
    if str((token_data or {}).get("role") or "").strip().lower() != "indemnitor":
        return _err(403, "role_not_indemnitor")
    body = body if isinstance(body, dict) else {}
    send_copy = body.get("send_copy") is True
    channel = str(body.get("channel") or "imessage").strip().lower()
    if channel not in ("imessage", "email"):
        channel = "imessage"
    booking = str((token_data or {}).get("booking_number") or "").strip()
    if not booking:
        return _err(409, "missing_booking_number")

    try:
        case, active = await resolve_bond_pair(booking)
    except BondAmbiguous:
        return _err(409, "bond_ambiguous")
    except Exception as exc:
        logger.warning("[portal_invoice] lookup failed err_type=%s", type(exc).__name__)
        return _err(503, "invoicing_unavailable")
    if not case and not active:
        return _err(404, "bond_not_found")
    if is_paid(case, active):
        return _err(409, "already_paid")

    due = confirmed_due_cents(case, active)
    if due is None and not any(confirmation_complete(d) for d in (case, active)) and auto_confirm_enabled():
        try:
            auto = await auto_confirm_premium(booking=booking, bond_case=case, active=active)
        except PremiumConfirmationError as exc:
            logger.warning("[portal_invoice] auto confirm refused booking=%s code=%s", booking, exc.error)
            if exc.status >= 500:
                return _err(503, "invoicing_unavailable")
            if exc.error == "confirmation_race":
                return _err(409, "needs_review")
            return _err(409, "premium_unconfirmed")
        if not auto.get("ok"):
            logger.info("[portal_invoice] auto confirm skipped booking=%s reason=%s", booking, auto.get("skipped"))
            if auto.get("skipped") in ("bond_amount_ambiguous", "bond_amount_unparseable"):
                return _err(409, "needs_review")
        try:
            case, active = await resolve_bond_pair(booking)
        except BondAmbiguous:
            return _err(409, "bond_ambiguous")
        except Exception:
            return _err(503, "invoicing_unavailable")
        due = confirmed_due_cents(case, active)
    if due is None:
        if any(confirmation_complete(d) for d in (case, active)):
            return _err(409, "amount_mismatch")
        if _estimate_only(case, active):
            return _err(409, "premium_estimate_unconfirmed")
        return _err(409, "premium_unconfirmed")

    link, code = verified_invoice_link(case, active, booking, due)
    if code == "invoice_amount_unproven":
        return _err(409, "amount_mismatch")
    if code:
        return _err(409, "needs_review")

    bond_id = _create_bond_id(case, active)
    created = False
    if not link:
        if not ss.live_http_enabled():
            return _err(503, "invoicing_unavailable")
        if not bond_id:
            return _err(404, "bond_not_found")
        try:
            result = await ss.create_locked_invoice(bond_id)
        except Exception as exc:
            status, out = map_invoice_error(exc)
            logger.warning(
                "[portal_invoice] create failed booking=%s error=%s", booking, out.get("error")
            )
            return status, out
        if result.get("in_progress"):
            return _err(202, "in_progress", retry_after_seconds=3)
        if not result.get("payment_link"):
            return _err(409, "needs_review")
        created = not result.get("idempotent")
        try:
            case, active = await resolve_bond_pair(booking)
        except BondAmbiguous:
            return _err(409, "bond_ambiguous")
        except Exception:
            return _err(503, "invoicing_unavailable")
        link, code = verified_invoice_link(case, active, booking, due)
        if code == "invoice_amount_unproven":
            return _err(409, "amount_mismatch")
        if not link:
            return _err(409, "needs_review")

    dispatch = await _dispatch_copy(bond_id, channel, send_copy)
    return 200, {
        "ok": True,
        "booking_number": booking,
        "invoice_number": booking,
        "payment_url": link,
        "amount_cents": due,
        "amount": cents_to_amount(due),
        "confirmed_by": confirmed_by(case, active),
        "status": "unpaid",
        "idempotent": not created,
        "created": created,
        "dispatch": dispatch,
    }
