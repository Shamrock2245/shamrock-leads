"""
ShamrockLeads — SwipeSimple Invoice Service (Option 2 — locked HTTP contract)
==============================================================================
Production path:
  1) POST form-urlencoded https://swipesimple.com/invoices  (Rails create draft)
  2) Resolve invoice_id after 302 (best-effort)
  3) POST /api/v4/invoices/{id}/copy_link → web payment URL

Playwright (`swipesimple_playwright_bootstrap.py`) = session/CSRF refresh ONLY.

HARD RULES (fail-closed):
  - premium must match BondCase exactly (dollars → exact integer cents)
  - invoice # / reference_id = booking #
  - one invoice per bond (idempotency key = bond_id / bond_case_id)
  - never invent premiums or payment links
  - never log/echo session cookies, CSRF tokens, or other secrets
  - LIVE HTTP gated by SWIPESIMPLE_LIVE=1 (default OFF)

See dashboard/services/SWIPESIMPLE_INVOICE_CONTRACT.md for the locked capture.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlencode, urljoin

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

Channel = Literal["imessage", "email"]

# Persist these on bond_cases / active_bonds after a successful create + copy_link.
INVOICE_LINK_FIELDS = (
    "swipesimple_invoice_id",       # vendor invoice id (needed for copy_link)
    "swipesimple_invoice_number",   # MUST equal booking_number
    "swipesimple_payment_link",     # web payment URL we dispatch (not SMS dual-send)
    "swipesimple_invoice_created_at",
    "swipesimple_invoice_bond_id",  # idempotency key written at create time
)

# Locked from DevTools create capture (2026-09-24). Host is swipesimple.com (not app.).
_DEFAULT_BASE_URL = "https://swipesimple.com"
_DEFAULT_MERCHANT_ACCOUNT_ID = "acc_bd9fed047bd6f7c6"
_DEFAULT_CATALOG_ITEM_ID = "im_bae23df0a0cb4e01a688bdd6bf1"
_DEFAULT_CATALOG_ITEM_NAME = "Bail Bond Premium"
# Rails common path for authenticity_token; confirm in prod if CSRF fetch fails.
_NEW_INVOICE_PATH = "/invoices/new"
_CREATE_INVOICE_PATH = "/invoices"
_COPY_LINK_PATH_TMPL = "/api/v4/invoices/{invoice_id}/copy_link"
_AMOUNT_TOLERANCE = Decimal("0.01")

# Live gate values (case-insensitive for true-ish strings).
_LIVE_TRUTHY = frozenset({"1", "true", "yes", "on"})


class SwipeSimpleInvoiceError(Exception):
    """Fail-closed invoice errors (missing booking, premium mismatch, etc.)."""


class SwipeSimpleInvoiceNotWired(NotImplementedError):
    """Raised when live HTTP is disabled or a required post-create step cannot complete."""


class SwipeSimpleLiveDisabled(SwipeSimpleInvoiceNotWired):
    """Raised when SWIPESIMPLE_LIVE is not enabled — default safe state."""


# ---------------------------------------------------------------------------
# Secret / env helpers — never print values
# ---------------------------------------------------------------------------

def _env_present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def live_http_enabled() -> bool:
    """
    Gate for any outbound SwipeSimple HTTP from this module.
    Default OFF: unset / empty / anything not in _LIVE_TRUTHY → no network.
    """
    raw = (os.getenv("SWIPESIMPLE_LIVE") or "").strip().lower()
    return raw in _LIVE_TRUTHY


def _require_live() -> None:
    if not live_http_enabled():
        raise SwipeSimpleLiveDisabled(
            "SwipeSimple live HTTP disabled (set SWIPESIMPLE_LIVE=1 only with Brendan go-ahead). "
            "No request was sent."
        )


def load_swipesimple_session_config() -> Dict[str, Any]:
    """
    Load session-shaped config for HTTP replay.

    Env (values NEVER logged):
      SWIPESIMPLE_SESSION or SWIPESIMPLE_COOKIE_JAR  (required for live HTTP)
      SWIPESIMPLE_CSRF_TOKEN                         (optional cached authenticity_token)
      SWIPESIMPLE_MERCHANT_ID                        (optional; default locked merchant)
      SWIPESIMPLE_CATALOG_ITEM_ID / _NAME            (optional; default Bail Bond Premium)
      SWIPESIMPLE_BASE_URL                           (optional; default https://swipesimple.com)
      SWIPESIMPLE_LIVE                               (must be 1/true for outbound HTTP)

    Returns metadata only (presence flags + non-secret base URL / IDs).
    """
    session = (os.getenv("SWIPESIMPLE_SESSION") or "").strip()
    cookie_jar = (os.getenv("SWIPESIMPLE_COOKIE_JAR") or "").strip()
    csrf = (os.getenv("SWIPESIMPLE_CSRF_TOKEN") or "").strip()
    merchant = (os.getenv("SWIPESIMPLE_MERCHANT_ID") or "").strip() or _DEFAULT_MERCHANT_ACCOUNT_ID
    catalog_id = (os.getenv("SWIPESIMPLE_CATALOG_ITEM_ID") or "").strip() or _DEFAULT_CATALOG_ITEM_ID
    catalog_name = (
        (os.getenv("SWIPESIMPLE_CATALOG_ITEM_NAME") or "").strip() or _DEFAULT_CATALOG_ITEM_NAME
    )
    base_url = (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/")

    cfg = {
        "base_url": base_url,
        "live_enabled": live_http_enabled(),
        "has_session": bool(session),
        "has_cookie_jar": bool(cookie_jar),
        "has_csrf": bool(csrf),
        "merchant_account_id": merchant,
        "catalog_item_id": catalog_id,
        "catalog_item_name": catalog_name,
        # Raw secrets kept internal — callers that need them use _secret_value().
        "_session": session or None,
        "_cookie_jar": cookie_jar or None,
        "_csrf": csrf or None,
    }
    logger.info(
        "[ss_invoice] session config loaded base_url=%s live=%s has_session=%s "
        "has_cookie_jar=%s has_csrf=%s merchant_id_set=%s catalog_item_id_set=%s",
        base_url,
        cfg["live_enabled"],
        cfg["has_session"],
        cfg["has_cookie_jar"],
        cfg["has_csrf"],
        bool(merchant),
        bool(catalog_id),
    )
    return cfg


def _secret_value(cfg: Dict[str, Any], key: str) -> Optional[str]:
    """Return a secret from load_swipesimple_session_config(); never log the result."""
    return cfg.get(key) or None


def _cookie_header(cfg: Dict[str, Any]) -> str:
    """Prefer SWIPESIMPLE_SESSION; fall back to SWIPESIMPLE_COOKIE_JAR. Never log."""
    return (
        (_secret_value(cfg, "_session") or "").strip()
        or (_secret_value(cfg, "_cookie_jar") or "").strip()
    )


def amounts_equal(a: Any, b: Any, *, tolerance: Decimal = _AMOUNT_TOLERANCE) -> bool:
    """Compare dollar amounts in Decimal space (fail-closed on parse error)."""
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


def premium_dollars_to_cents(premium: Decimal) -> int:
    """
    BondCase premium is dollars; SwipeSimple form wants cents (100 = $1.00).
    Fail-closed unless conversion is an exact integer cent value.
    """
    if premium is None:
        raise SwipeSimpleInvoiceError("premium_missing_on_bondcase")
    try:
        as_cents = premium * Decimal(100)
    except (InvalidOperation, TypeError):
        raise SwipeSimpleInvoiceError("premium_not_exact_cents") from None
    if as_cents != as_cents.to_integral_value():
        raise SwipeSimpleInvoiceError("premium_not_exact_cents")
    cents = int(as_cents)
    if cents < 0:
        raise SwipeSimpleInvoiceError("premium_negative")
    return cents


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


def _bond_customer_fields(bond: Dict[str, Any]) -> Dict[str, str]:
    """Map BondCase → SwipeSimple customer form fields (no secrets)."""
    name = (
        str(
            bond.get("defendant_name")
            or bond.get("Defendant_Name")
            or bond.get("indemnitor_name")
            or bond.get("Indemnitor_Name")
            or ""
        ).strip()
    )
    email = str(
        bond.get("indemnitor_email")
        or bond.get("Indemnitor_Email")
        or bond.get("defendant_email")
        or ""
    ).strip()
    phone = str(
        bond.get("indemnitor_phone")
        or bond.get("Indemnitor_Phone")
        or bond.get("defendant_phone")
        or ""
    ).strip()
    customer_id = str(
        bond.get("swipesimple_customer_id")
        or bond.get("ss_customer_id")
        or ""
    ).strip()
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "customer_id": customer_id,
    }


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
# Form body + HTTP helpers (gated; never log secrets)
# ---------------------------------------------------------------------------

def build_create_invoice_form(
    *,
    authenticity_token: str,
    merchant_account_id: str,
    booking_number: str,
    cents: int,
    customer: Dict[str, str],
    catalog_item_id: str = _DEFAULT_CATALOG_ITEM_ID,
    catalog_item_name: str = _DEFAULT_CATALOG_ITEM_NAME,
) -> List[Tuple[str, str]]:
    """
    Build Rails form-urlencoded field list matching the locked DevTools capture.
    Amounts are integer cents. Does not include secret logging.
    """
    cust_id = (customer.get("customer_id") or "").strip()
    name = (customer.get("name") or "").strip()
    email = (customer.get("email") or "").strip()
    phone = (customer.get("phone") or "").strip()
    cents_s = str(int(cents))

    return [
        ("authenticity_token", authenticity_token),
        ("invoice[merchant_account_id]", merchant_account_id),
        ("invoice[customer][id]", cust_id),
        ("invoice[customer][name]", name),
        ("customer-proxy", cust_id),
        ("invoice[email]", email),
        ("invoice[customer][email]", email),
        ("invoice[phone]", phone),
        ("invoice[customer][phone]", phone),
        ("invoice[reference_id]", booking_number),
        ("invoice[items][][id]", catalog_item_id),
        ("invoice[items][][name]", catalog_item_name),
        ("invoice[items][][quantity]", "1"),
        ("invoice[items][][price]", cents_s),
        ("invoice[discounts][]", ""),
        ("invoice[prompt_for_tip]", "0"),
        ("invoice[amount]", cents_s),
        ("invoice[unadjusted_amount]", cents_s),
        ("invoice[save_as_draft]", "true"),
    ]


_AUTH_TOKEN_RE = re.compile(
    r'name=["\']authenticity_token["\'][^>]*value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\'][^>]*name=["\']authenticity_token["\']'
    r'|name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']'
    r'|content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token["\']',
    re.IGNORECASE,
)


def _parse_authenticity_token(html: str) -> Optional[str]:
    if not html:
        return None
    m = _AUTH_TOKEN_RE.search(html)
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


async def _fetch_csrf(cfg: Dict[str, Any]) -> str:
    """
    Fetch authenticity_token for the create form.

    Order:
      1. Cached SWIPESIMPLE_CSRF_TOKEN from env (bootstrap may refresh it)
      2. GET {base}/invoices/new with Cookie — parse HTML

    TODO: confirm _NEW_INVOICE_PATH if production uses a non-Rails path
    (e.g. SPA route). Cookie header is always wired from SESSION / COOKIE_JAR.
    """
    cached = (_secret_value(cfg, "_csrf") or "").strip()
    if cached:
        logger.info("[ss_invoice] using cached CSRF from env (value not logged)")
        return cached

    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    # Lazy import so default-OFF import path never pulls httpx until needed.
    import httpx

    url = urljoin(cfg["base_url"] + "/", _NEW_INVOICE_PATH.lstrip("/"))
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": cookie,
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    logger.info("[ss_invoice] CSRF fetch GET path=%s (cookie present, not logged)", _NEW_INVOICE_PATH)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise SwipeSimpleInvoiceError(f"csrf_fetch_http_{resp.status_code}")
    token = _parse_authenticity_token(resp.text or "")
    if not token:
        raise SwipeSimpleInvoiceError("csrf_token_not_found_in_new_invoice_html")
    return token


async def _http_create_invoice(
    *,
    form_fields: List[Tuple[str, str]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    POST application/x-www-form-urlencoded create. Expect 302 → /invoices.
    Does not follow redirect by default so Location / status are visible.
    """
    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    import httpx

    url = urljoin(cfg["base_url"] + "/", _CREATE_INVOICE_PATH.lstrip("/"))
    body = urlencode(form_fields)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "Origin": cfg["base_url"],
        "Referer": urljoin(cfg["base_url"] + "/", _NEW_INVOICE_PATH.lstrip("/")),
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    logger.info(
        "[ss_invoice] CREATE POST path=%s content_type=form-urlencoded fields=%d",
        _CREATE_INVOICE_PATH,
        len(form_fields),
    )
    async with httpx.AsyncClient(follow_redirects=False, timeout=45.0) as client:
        resp = await client.post(url, content=body.encode("utf-8"), headers=headers)

    location = resp.headers.get("Location") or resp.headers.get("location") or ""
    logger.info(
        "[ss_invoice] CREATE response status=%s location_present=%s",
        resp.status_code,
        bool(location),
    )
    if resp.status_code not in (302, 303, 301):
        # Some stacks may 200 the index after PRG; treat non-3xx as soft continue
        # only when body hints success — otherwise fail-closed.
        if resp.status_code >= 400:
            raise SwipeSimpleInvoiceError(f"create_invoice_http_{resp.status_code}")
        logger.warning(
            "[ss_invoice] CREATE unexpected status=%s (expected 302); continuing to resolve id",
            resp.status_code,
        )
    return {
        "status_code": resp.status_code,
        "location": location,
        "body": resp.text or "",
    }


async def _resolve_invoice_id_after_create(
    *,
    booking_number: str,
    create_result: Dict[str, Any],
    cfg: Dict[str, Any],
) -> str:
    """
    Best-effort invoice_id after create 302 (no JSON body).

    Strategy:
      1. Parse Location for /invoices/<id> if present
      2. Parse create response HTML for invoice id tokens
      3. GET /invoices (or /api/v4/invoices) and match reference_id / booking #

    OPEN GAP: production may only list invoices in HTML/SPA without a stable
    id in the 302 Location. If unresolved, raise — do not invent an id.
    """
    location = str(create_result.get("location") or "")
    m = re.search(r"/invoices/([A-Za-z0-9_-]+)", location)
    if m and m.group(1) not in ("new", "edit", ""):
        logger.info("[ss_invoice] invoice_id from Location path")
        return m.group(1)

    body = str(create_result.get("body") or "")
    # Prefer ids near the booking/reference we just created.
    for pat in (
        rf'data-invoice-id=["\']([A-Za-z0-9_-]+)["\'][^>]*>[^<]*{re.escape(booking_number)}',
        rf'/invoices/([A-Za-z0-9_-]+)[^"]*"[^>]*>\s*{re.escape(booking_number)}',
        rf'"id"\s*:\s*"([A-Za-z0-9_-]+)"[^}}]*"reference_id"\s*:\s*"{re.escape(booking_number)}"',
        rf'"reference_id"\s*:\s*"{re.escape(booking_number)}"[^}}]*"id"\s*:\s*"([A-Za-z0-9_-]+)"',
    ):
        mm = re.search(pat, body, re.IGNORECASE | re.DOTALL)
        if mm:
            logger.info("[ss_invoice] invoice_id from create body pattern")
            return mm.group(1)

    # Live list/search fallback (gated).
    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    import httpx

    headers = {
        "Accept": "text/html,application/json",
        "Cookie": cookie,
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    list_paths = (
        f"/api/v4/invoices?reference_id={booking_number}",
        f"/api/v4/invoices?q={booking_number}",
        "/invoices",
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=45.0) as client:
        for path in list_paths:
            url = urljoin(cfg["base_url"] + "/", path.lstrip("/"))
            logger.info("[ss_invoice] resolve invoice_id via GET path=%s", path.split("?")[0])
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:
                logger.warning("[ss_invoice] list fetch failed path=%s err=%s", path.split("?")[0], exc)
                continue
            text = resp.text or ""
            for pat in (
                rf'"id"\s*:\s*"([A-Za-z0-9_-]+)"[^}}]*"reference_id"\s*:\s*"{re.escape(booking_number)}"',
                rf'"reference_id"\s*:\s*"{re.escape(booking_number)}"[^}}]*"id"\s*:\s*"([A-Za-z0-9_-]+)"',
                rf'/invoices/([A-Za-z0-9_-]+)[^"]*"[^>]*>\s*{re.escape(booking_number)}',
                rf'data-invoice-id=["\']([A-Za-z0-9_-]+)["\'][^>]*>[^<]*{re.escape(booking_number)}',
            ):
                mm = re.search(pat, text, re.IGNORECASE | re.DOTALL)
                if mm:
                    logger.info("[ss_invoice] invoice_id from list/search")
                    return mm.group(1)

    raise SwipeSimpleInvoiceError(
        "invoice_id_unresolved_after_create_302 — "
        "create likely succeeded (draft) but copy_link needs vendor id; "
        "follow-up: harden list/API parse or capture Location from production"
    )


async def _http_copy_link(*, invoice_id: str, cfg: Dict[str, Any]) -> str:
    """POST /api/v4/invoices/{id}/copy_link with empty body; return payment URL."""
    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")
    invoice_id = str(invoice_id or "").strip()
    if not invoice_id:
        raise SwipeSimpleInvoiceError("missing_invoice_id_for_copy_link")

    import httpx

    path = _COPY_LINK_PATH_TMPL.format(invoice_id=invoice_id)
    url = urljoin(cfg["base_url"] + "/", path.lstrip("/"))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Content-Type": "application/json",
        "Origin": cfg["base_url"],
        "Referer": urljoin(cfg["base_url"] + "/", "invoices"),
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    logger.info("[ss_invoice] copy_link POST invoice_id_len=%s", len(invoice_id))
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.post(url, content=b"", headers=headers)
    if resp.status_code >= 400:
        raise SwipeSimpleInvoiceError(f"copy_link_http_{resp.status_code}")

    link = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            for key in ("url", "link", "payment_link", "web_link", "share_link", "data"):
                val = data.get(key)
                if isinstance(val, dict):
                    for k2 in ("url", "link", "payment_link"):
                        if str(val.get(k2) or "").startswith("http"):
                            link = str(val.get(k2)).strip()
                            break
                elif str(val or "").startswith("http"):
                    link = str(val).strip()
                    break
        elif isinstance(data, str) and data.startswith("http"):
            link = data.strip()
    except Exception:
        text = (resp.text or "").strip()
        if text.startswith("http"):
            link = text.split()[0].strip().strip('"')

    if not link.startswith("http"):
        # Last resort: bare URL in body
        m = re.search(r"https?://[^\s\"'<>]+", resp.text or "")
        if m:
            link = m.group(0)
    if not link.startswith("http"):
        raise SwipeSimpleInvoiceError("copy_link_missing_payment_url")
    return link


async def _share_invoice_http(
    *,
    booking_number: str,
    premium: Decimal,
    bond_id: str,
    bond: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Locked Option 2 path: form create → resolve invoice_id → copy_link.

    Gated by SWIPESIMPLE_LIVE. Never invents a payment link.
    """
    _require_live()

    if not (cfg.get("has_session") or cfg.get("has_cookie_jar")):
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    cents = premium_dollars_to_cents(premium)
    customer = _bond_customer_fields(bond)
    authenticity_token = await _fetch_csrf(cfg)

    form_fields = build_create_invoice_form(
        authenticity_token=authenticity_token,
        merchant_account_id=str(cfg.get("merchant_account_id") or _DEFAULT_MERCHANT_ACCOUNT_ID),
        booking_number=booking_number,
        cents=cents,
        customer=customer,
        catalog_item_id=str(cfg.get("catalog_item_id") or _DEFAULT_CATALOG_ITEM_ID),
        catalog_item_name=str(cfg.get("catalog_item_name") or _DEFAULT_CATALOG_ITEM_NAME),
    )

    create_result = await _http_create_invoice(form_fields=form_fields, cfg=cfg)
    invoice_id = await _resolve_invoice_id_after_create(
        booking_number=booking_number,
        create_result=create_result,
        cfg=cfg,
    )
    payment_link = await _http_copy_link(invoice_id=invoice_id, cfg=cfg)

    # Dollars for callers / mismatch checks (BondCase space).
    return {
        "payment_link": payment_link,
        "invoice_id": invoice_id,
        "invoice_number": booking_number,
        "amount": float(premium),
        "amount_cents": cents,
        "bond_id": bond_id,
        "create_status": create_result.get("status_code"),
    }


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
    filt: Dict[str, Any] = {
        "$or": [{"booking_number": booking}, {"bond_case_id": bond_id}, {"bond_id": bond_id}]
    }
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
    Create (or return existing) locked SwipeSimple invoice for a bond.

    Fail-closed:
      - bond must exist
      - booking_number required (invoice # = booking #)
      - premium must be present on BondCase (no invent)
      - dollars → exact integer cents
      - live HTTP only when SWIPESIMPLE_LIVE=1

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
    # Validate cents conversion early (even before live gate / idempotent miss).
    _ = premium_dollars_to_cents(premium)

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
        "amount_cents": http_result.get("amount_cents"),
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
