"""
ShamrockLeads — SwipeSimple Share-Invoice Service (Option 2 scaffold)
====================================================================
Production path: captured Share Invoice HTTP replay (NOT Playwright create).

Day-to-day automation target:
  bond → locked invoice → web payment link → BlueBubbles/email → reconcile PAID

HARD RULES (fail-closed):
  - premium must match BondCase exactly (no invented amounts)
  - invoice # = booking #
  - one invoice per bond (idempotency key = bond_id / bond_case_id)
  - never invent premiums or payment links
  - never log/echo session cookies, CSRF tokens, or other secrets

STATUS: stubs only. Share Invoice HTTP body awaits Brendan DevTools cURL capture.
Do NOT call SwipeSimple live from this module until capture is wired.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Literal, Optional, Tuple

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

Channel = Literal["imessage", "email"]

# Persist these on bond_cases / active_bonds after a successful create (documented
# contract — fill values only after real Share Invoice response is parsed).
INVOICE_LINK_FIELDS = (
    "swipesimple_invoice_id",       # vendor invoice id if returned
    "swipesimple_invoice_number",   # MUST equal booking_number
    "swipesimple_payment_link",     # web payment URL we dispatch (not SMS dual-send)
    "swipesimple_invoice_created_at",
    "swipesimple_invoice_bond_id",  # idempotency key written at create time
)

_DEFAULT_BASE_URL = "https://app.swipesimple.com"
_AMOUNT_TOLERANCE = Decimal("0.01")


class SwipeSimpleInvoiceError(Exception):
    """Fail-closed invoice errors (missing booking, premium mismatch, etc.)."""


class SwipeSimpleInvoiceNotWired(NotImplementedError):
    """Raised until captured Share Invoice HTTP is plugged into _share_invoice_http."""


# ---------------------------------------------------------------------------
# Secret / env helpers — never print values
# ---------------------------------------------------------------------------

def _env_present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def load_swipesimple_session_config() -> Dict[str, Any]:
    """
    Load session-shaped config for HTTP replay.

    Env (values NEVER logged):
      SWIPESIMPLE_SESSION or SWIPESIMPLE_COOKIE_JAR  (required for live HTTP)
      SWIPESIMPLE_CSRF_TOKEN                         (optional)
      SWIPESIMPLE_MERCHANT_ID                        (optional)
      SWIPESIMPLE_BASE_URL                           (optional; default app host)

    Returns metadata only (presence flags + non-secret base URL).
    """
    session = (os.getenv("SWIPESIMPLE_SESSION") or "").strip()
    cookie_jar = (os.getenv("SWIPESIMPLE_COOKIE_JAR") or "").strip()
    csrf = (os.getenv("SWIPESIMPLE_CSRF_TOKEN") or "").strip()
    merchant = (os.getenv("SWIPESIMPLE_MERCHANT_ID") or "").strip()
    base_url = (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/")

    cfg = {
        "base_url": base_url,
        "has_session": bool(session),
        "has_cookie_jar": bool(cookie_jar),
        "has_csrf": bool(csrf),
        "has_merchant_id": bool(merchant),
        # Raw values kept internal — callers that need them use _secret_value().
        "_session": session or None,
        "_cookie_jar": cookie_jar or None,
        "_csrf": csrf or None,
        "_merchant_id": merchant or None,
    }
    logger.info(
        "[ss_invoice] session config loaded base_url=%s has_session=%s "
        "has_cookie_jar=%s has_csrf=%s has_merchant_id=%s",
        base_url,
        cfg["has_session"],
        cfg["has_cookie_jar"],
        cfg["has_csrf"],
        cfg["has_merchant_id"],
    )
    return cfg


def _secret_value(cfg: Dict[str, Any], key: str) -> Optional[str]:
    """Return a secret from load_swipesimple_session_config(); never log the result."""
    return cfg.get(key) or None


def amounts_equal(a: Any, b: Any, *, tolerance: Decimal = _AMOUNT_TOLERANCE) -> bool:
    """Compare dollar amounts in Decimal cents space (fail-closed on parse error)."""
    try:
        da = Decimal(str(a).strip().replace(",", "").replace("$", ""))
        db = Decimal(str(b).strip().replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return False
    return abs(da - db) <= tolerance


def money_to_decimal(value: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(value).strip().replace(",", "").replace("$", ""))
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None


def validate_booking_number(booking_number: Any) -> str:
    booking = str(booking_number or "").strip()
    if not booking:
        raise SwipeSimpleInvoiceError("missing_booking_number")
    return booking


# ---------------------------------------------------------------------------
# Bond / BondCase loaders
# ---------------------------------------------------------------------------

async def _load_bond_by_id(bond_id: str) -> Optional[Dict[str, Any]]:
    """
    Resolve BondCase / active bond by bond_case_id, bond_id, or ObjectId string.
    Prefer bond_cases, fall back to active_bonds (production dual-collection reality).
    """
    bond_id = str(bond_id or "").strip()
    if not bond_id:
        return None

    clauses: list[dict] = [
        {"bond_case_id": bond_id},
        {"bond_id": bond_id},
    ]
    try:
        from bson import ObjectId

        if ObjectId.is_valid(bond_id):
            clauses.append({"_id": ObjectId(bond_id)})
    except Exception:
        pass

    query = {"$or": clauses}
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            doc = await get_collection(coll_name).find_one(query)
            if doc:
                doc["_collection"] = coll_name
                return doc
        except Exception as exc:
            logger.warning("[ss_invoice] %s lookup failed: %s", coll_name, exc)
    return None


def _bond_premium(bond: Dict[str, Any]) -> Optional[Decimal]:
    """Authoritative premium from BondCase fields — never invent."""
    for key in ("premium_amount", "total_premium", "premium", "Premium_Amount"):
        if bond.get(key) is not None and str(bond.get(key)).strip() != "":
            return money_to_decimal(bond.get(key))
    return None


def _bond_booking(bond: Dict[str, Any]) -> str:
    for key in ("booking_number", "Booking_Number", "defendant_booking_number"):
        val = str(bond.get(key) or "").strip()
        if val:
            return val
    return ""


def _existing_payment_link(bond: Dict[str, Any]) -> Optional[str]:
    for key in ("swipesimple_payment_link", "invoice_payment_link", "payment_link"):
        link = str(bond.get(key) or "").strip()
        if link.startswith("http"):
            return link
    return None


def _is_paid(bond: Dict[str, Any]) -> bool:
    if bond.get("premium_paid") is True or bond.get("payment_received") is True:
        return True
    status = str(bond.get("payment_status") or bond.get("last_payment_status") or "").strip().lower()
    return status in {"paid", "collected", "complete", "completed", "approved", "received"}


# ---------------------------------------------------------------------------
# HTTP Share Invoice placeholder (Option 2 — awaiting cURL capture)
# ---------------------------------------------------------------------------

async def _share_invoice_http(
    *,
    booking_number: str,
    premium: Decimal,
    bond_id: str,
    bond: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Placeholder for captured Share Invoice HTTP replay.

    TODO(Brendan cURL capture): fill URL, method, headers (names), JSON body keys
    in SWIPESIMPLE_INVOICE_CONTRACT.md, then implement the request here.
    Until then this MUST raise — never invent a payment link.
    """
    if not (cfg.get("has_session") or cfg.get("has_cookie_jar")):
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    # Intentionally unused until capture arrives — keep signature stable.
    _ = (booking_number, premium, bond_id, bond, _secret_value(cfg, "_session"))

    raise SwipeSimpleInvoiceNotWired(
        "Share Invoice HTTP body not wired — awaiting Brendan DevTools cURL capture "
        "(see dashboard/services/SWIPESIMPLE_INVOICE_CONTRACT.md). "
        "Playwright is session-refresh only; do not use it as primary create path."
    )


async def _persist_invoice_fields(
    bond: Dict[str, Any],
    *,
    bond_id: str,
    booking_number: str,
    payment_link: str,
    invoice_id: str = "",
) -> None:
    """Write link fields onto bond_cases + active_bonds by booking_number / bond id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    patch = {
        "swipesimple_invoice_id": invoice_id or None,
        "swipesimple_invoice_number": booking_number,
        "swipesimple_payment_link": payment_link,
        "swipesimple_invoice_created_at": now_iso,
        "swipesimple_invoice_bond_id": bond_id,
        "payment_status": bond.get("payment_status") or "sent",
        "updated_at": now_iso,
    }
    booking = _bond_booking(bond) or booking_number
    filt: Dict[str, Any] = {"$or": [{"booking_number": booking}, {"bond_case_id": bond_id}, {"bond_id": bond_id}]}
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            await get_collection(coll_name).update_one(filt, {"$set": patch})
        except Exception as exc:
            logger.warning("[ss_invoice] persist on %s failed: %s", coll_name, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_locked_invoice(bond_id: str) -> Dict[str, Any]:
    """
    Create (or return existing) locked SwipeSimple Share Invoice for a bond.

    Fail-closed:
      - bond must exist
      - booking_number required (invoice # = booking #)
      - premium must be present on BondCase (no invent / no 10% guess here)
      - if caller later supplies an amount, it must match BondCase exactly

    Idempotent: if swipesimple_payment_link already stored for this bond_id,
    return the existing link without another HTTP create.
    """
    bond_id = str(bond_id or "").strip()
    if not bond_id:
        raise SwipeSimpleInvoiceError("missing_bond_id")

    bond = await _load_bond_by_id(bond_id)
    if not bond:
        raise SwipeSimpleInvoiceError("bond_not_found")

    booking_number = validate_booking_number(_bond_booking(bond))
    premium = _bond_premium(bond)
    if premium is None:
        raise SwipeSimpleInvoiceError("premium_missing_on_bondcase")

    existing = _existing_payment_link(bond)
    if existing and (
        str(bond.get("swipesimple_invoice_bond_id") or "") == bond_id
        or str(bond.get("swipesimple_invoice_number") or "") == booking_number
        or bond.get("swipesimple_payment_link")
    ):
        logger.info(
            "[ss_invoice] idempotent hit bond_id=%s booking=%s",
            bond_id,
            booking_number,
        )
        return {
            "ok": True,
            "idempotent": True,
            "bond_id": bond_id,
            "booking_number": booking_number,
            "invoice_number": booking_number,
            "premium_amount": float(premium),
            "payment_link": existing,
            "swipesimple_invoice_id": bond.get("swipesimple_invoice_id"),
        }

    cfg = load_swipesimple_session_config()

    # Placeholder HTTP — raises SwipeSimpleInvoiceNotWired until capture wired.
    http_result = await _share_invoice_http(
        booking_number=booking_number,
        premium=premium,
        bond_id=bond_id,
        bond=bond,
        cfg=cfg,
    )

    payment_link = str(http_result.get("payment_link") or "").strip()
    if not payment_link.startswith("http"):
        raise SwipeSimpleInvoiceError("share_invoice_missing_payment_link")

    # Fail-closed: vendor amount must match BondCase if returned
    vendor_amount = http_result.get("amount")
    if vendor_amount is not None and not amounts_equal(vendor_amount, premium):
        raise SwipeSimpleInvoiceError("premium_mismatch_vs_bondcase")

    await _persist_invoice_fields(
        bond,
        bond_id=bond_id,
        booking_number=booking_number,
        payment_link=payment_link,
        invoice_id=str(http_result.get("invoice_id") or ""),
    )

    return {
        "ok": True,
        "idempotent": False,
        "bond_id": bond_id,
        "booking_number": booking_number,
        "invoice_number": booking_number,
        "premium_amount": float(premium),
        "payment_link": payment_link,
        "swipesimple_invoice_id": http_result.get("invoice_id"),
    }


async def dispatch_invoice(
    bond_id: str,
    channel: Channel = "imessage",
) -> Dict[str, Any]:
    """
    Dispatch stored payment link via BlueBubbles (imessage) or email.

    Only runs after create_locked_invoice succeeded / link is stored.
    STUB: does not send — builds payload and returns without calling BB/Gmail.
    Prefer web payment link; no dual SwipeSimple SMS unless asked later.
    """
    if channel not in ("imessage", "email"):
        raise SwipeSimpleInvoiceError("invalid_channel")

    bond = await _load_bond_by_id(bond_id)
    if not bond:
        raise SwipeSimpleInvoiceError("bond_not_found")

    payment_link = _existing_payment_link(bond)
    if not payment_link:
        raise SwipeSimpleInvoiceError("payment_link_not_stored_create_first")

    booking_number = validate_booking_number(_bond_booking(bond))
    premium = _bond_premium(bond)
    if premium is None:
        raise SwipeSimpleInvoiceError("premium_missing_on_bondcase")

    defendant = (
        bond.get("defendant_name")
        or bond.get("Defendant_Name")
        or ""
    )
    phone = (
        bond.get("indemnitor_phone")
        or bond.get("Indemnitor_Phone")
        or ""
    )
    email = (
        bond.get("indemnitor_email")
        or bond.get("Indemnitor_Email")
        or ""
    )

    # Copy shape mirrors packet_payment_link_service (amount in message body).
    body = (
        f"Shamrock Bail Bonds — premium payment for {defendant or 'your bond'} "
        f"(booking {booking_number}). Amount due: ${premium:,.2f}.\n"
        f"Pay Online via SwipeSimple:\n{payment_link}\n"
    )

    logger.info(
        "[ss_invoice] dispatch STUB channel=%s bond_id=%s booking=%s "
        "has_phone=%s has_email=%s (not sending)",
        channel,
        bond_id,
        booking_number,
        bool(str(phone).strip()),
        bool(str(email).strip()),
    )

    # TODO: wire send_message_universal / GmailReaderService.send_email after
    # create path is live. Do not send from this stub.
    return {
        "ok": True,
        "sent": False,
        "stub": True,
        "channel": channel,
        "bond_id": bond_id,
        "booking_number": booking_number,
        "payment_link": payment_link,
        "premium_amount": float(premium),
        "has_recipient": bool(str(phone).strip() if channel == "imessage" else str(email).strip()),
        "preview_body_chars": len(body),
        "message": "dispatch stub — BlueBubbles/email not invoked",
    }


async def reconcile_payment(
    booking_number: Optional[str] = None,
    receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Match a paid SwipeSimple receipt → bond PAID + ledger stub.

    Idempotent if bond already PAID / payment_status=paid.
    Prefer matching on booking_number (= invoice #). Receipt dict may carry
    amount / transaction_id from Gmail poller or webhook.
    """
    receipt = receipt or {}
    booking = validate_booking_number(
        booking_number or receipt.get("booking_number") or ""
    )

    bonds_col = get_collection("bond_cases")
    active_col = get_collection("active_bonds")
    bond = await bonds_col.find_one({"booking_number": booking})
    if not bond:
        bond = await active_col.find_one({"booking_number": booking})
    if not bond:
        raise SwipeSimpleInvoiceError("bond_not_found_for_booking")

    if _is_paid(bond):
        logger.info("[ss_invoice] reconcile idempotent already PAID booking=%s", booking)
        return {
            "ok": True,
            "idempotent": True,
            "booking_number": booking,
            "payment_status": "paid",
        }

    amount = money_to_decimal(receipt.get("amount"))
    expected = _bond_premium(bond)
    if amount is not None and expected is not None and not amounts_equal(amount, expected):
        # Fail-closed on mismatch — do not mark PAID
        raise SwipeSimpleInvoiceError("premium_mismatch_vs_bondcase")

    now_iso = datetime.now(timezone.utc).isoformat()
    txn = str(receipt.get("transaction_id") or "").strip() or f"SS-RECON-{booking}"

    payment_update = {
        "payment_status": "paid",
        "premium_paid": True,
        "payment_received": True,
        "last_payment_amount": float(amount) if amount is not None else (
            float(expected) if expected is not None else None
        ),
        "last_payment_at": now_iso,
        "last_payment_status": "paid",
        "last_transaction_id": txn,
        "last_payment_source": "swipesimple_invoice_reconcile",
        "updated_at": now_iso,
    }

    await bonds_col.update_one({"booking_number": booking}, {"$set": payment_update})
    await active_col.update_one({"booking_number": booking}, {"$set": payment_update})

    # Ledger stub — prefer LedgerService when available; soft-fail otherwise.
    ledger_txn = None
    try:
        from dashboard.services.ledger_service import LedgerService

        ledger_txn = await LedgerService.add_entry(
            {
                "booking_number": booking,
                "type": "payment",
                "category": "premium",
                "amount": payment_update["last_payment_amount"] or 0,
                "actor": "SwipeSimpleInvoiceService",
                "notes": "reconcile_payment stub",
                "stripe_swipe_ref": txn,
            }
        )
    except Exception as exc:
        logger.warning("[ss_invoice] ledger stub failed booking=%s: %s", booking, exc)

    logger.info(
        "[ss_invoice] reconcile PAID booking=%s ledger_txn=%s",
        booking,
        ledger_txn or "(none)",
    )
    return {
        "ok": True,
        "idempotent": False,
        "booking_number": booking,
        "payment_status": "paid",
        "transaction_id": txn,
        "ledger_transaction_id": ledger_txn,
    }


def assert_premium_matches_bondcase(
    bond: Dict[str, Any],
    candidate_amount: Any,
) -> Tuple[bool, Optional[str]]:
    """Helper for callers: (ok, error_code). Never invents a premium."""
    expected = _bond_premium(bond)
    if expected is None:
        return False, "premium_missing_on_bondcase"
    if not amounts_equal(candidate_amount, expected):
        return False, "premium_mismatch_vs_bondcase"
    return True, None
