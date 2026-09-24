"""
ShamrockLeads — SwipeSimple Invoice Service (Option 2 — locked HTTP contract)
==============================================================================
Production path:
  1) POST form-urlencoded https://swipesimple.com/invoices  (Rails create draft)
  2) Resolve invoice_id after 302 (best-effort; prefer /api/v4/invoices?reference_id=)
  3) POST /api/v4/invoices/{id}/copy_link → web payment URL

Playwright (`swipesimple_playwright_bootstrap.py`) = session/CSRF refresh ONLY.

HARD RULES (fail-closed):
  - premium must match BondCase exactly (dollars → exact integer cents)
  - invoice # / reference_id = booking #
  - one invoice per bond (idempotency key = bond_id / bond_case_id)
  - never invent premiums or payment links
  - never log/echo session cookies, CSRF tokens, or other secrets
  - LIVE HTTP gated by SWIPESIMPLE_LIVE=1 (default OFF)
  - Customer dispatch gated by SWIPESIMPLE_DISPATCH_LIVE=1 (default OFF / dry-run)

Paperwork Desk one-liner:
  from dashboard.services.swipesimple_invoice_service import maybe_issue_share_invoice_for_bond
  await maybe_issue_share_invoice_for_bond(bond_id, channel="imessage", source="paperwork_desk")

Brendan $0.01 smoke (no BondCase / no dispatch):
  python scripts/swipesimple_smoke_create.py

See dashboard/services/SWIPESIMPLE_INVOICE_CONTRACT.md and
dashboard/services/SWIPESIMPLE_PRODUCTION_CHECKLIST.md.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import quote, urlencode, urljoin

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
# Rails common path for authenticity_token; overridable via env.
_DEFAULT_NEW_INVOICE_PATH = "/invoices/new"
_CREATE_INVOICE_PATH = "/invoices"
_LIST_INVOICES_API = "/api/v4/invoices"
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


def dispatch_live_enabled() -> bool:
    """
    Gate for outbound BlueBubbles / email customer messages from dispatch_invoice.
    Default OFF: dry-run builds payload and logs intent only.
    Set SWIPESIMPLE_DISPATCH_LIVE=1 only after Brendan go-ahead (separate from HTTP LIVE).
    """
    raw = (os.getenv("SWIPESIMPLE_DISPATCH_LIVE") or "").strip().lower()
    return raw in _LIVE_TRUTHY


def new_invoice_path() -> str:
    """GET path for CSRF / authenticity_token HTML (env SWIPESIMPLE_NEW_INVOICE_PATH)."""
    raw = (os.getenv("SWIPESIMPLE_NEW_INVOICE_PATH") or "").strip()
    if not raw:
        return _DEFAULT_NEW_INVOICE_PATH
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/") or _DEFAULT_NEW_INVOICE_PATH


def share_invoice_on_promote_enabled() -> bool:
    """Opt-in hook for intake promote → Share Invoice (default OFF)."""
    raw = (os.getenv("SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE") or "").strip().lower()
    return raw in _LIVE_TRUTHY


def load_swipesimple_session_config() -> Dict[str, Any]:
    """
    Load session-shaped config for HTTP replay.

    Env (values NEVER logged):
      SWIPESIMPLE_SESSION or SWIPESIMPLE_COOKIE_JAR  (required for live HTTP)
      SWIPESIMPLE_CSRF_TOKEN                         (optional cached authenticity_token)
      SWIPESIMPLE_MERCHANT_ID                        (optional; default locked merchant)
      SWIPESIMPLE_CATALOG_ITEM_ID / _NAME            (optional; default Bail Bond Premium)
      SWIPESIMPLE_BASE_URL                           (optional; default https://swipesimple.com)
      SWIPESIMPLE_NEW_INVOICE_PATH                   (optional; default /invoices/new)
      SWIPESIMPLE_LIVE                               (must be 1/true for outbound HTTP)
      SWIPESIMPLE_DISPATCH_LIVE                      (must be 1/true to send BB/email)
      SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE           (opt-in intake promote hook)

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
    csrf_path = new_invoice_path()

    cfg = {
        "base_url": base_url,
        "new_invoice_path": csrf_path,
        "live_enabled": live_http_enabled(),
        "dispatch_live_enabled": dispatch_live_enabled(),
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
        "[ss_invoice] session config loaded base_url=%s new_invoice_path=%s live=%s "
        "dispatch_live=%s has_session=%s has_cookie_jar=%s has_csrf=%s "
        "merchant_id_set=%s catalog_item_id_set=%s",
        base_url,
        csrf_path,
        cfg["live_enabled"],
        cfg["dispatch_live_enabled"],
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
        {"booking_number": bond_id},
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
    """
    Map BondCase → SwipeSimple customer form fields (no secrets).

    Prefer indemnitor (payer) name/phone/email; fall back to defendant.
    Empty customer_id is OK: SwipeSimple accepts new-customer create via
    nested name/email/phone when invoice[customer][id] is blank (DevTools
    capture used a known cus_* id when one already existed).
    """
    name = (
        str(
            bond.get("indemnitor_name")
            or bond.get("Indemnitor_Name")
            or bond.get("defendant_name")
            or bond.get("Defendant_Name")
            or ""
        ).strip()
    )
    email = str(
        bond.get("indemnitor_email")
        or bond.get("Indemnitor_Email")
        or bond.get("defendant_email")
        or bond.get("Defendant_Email")
        or ""
    ).strip()
    phone = str(
        bond.get("indemnitor_phone")
        or bond.get("Indemnitor_Phone")
        or bond.get("defendant_phone")
        or bond.get("Defendant_Phone")
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
        "customer_id": customer_id,  # empty string OK for new customer
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
    Empty customer_id is intentional for new customers (name/email/phone still sent).
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


_AUTH_TOKEN_PATTERNS = (
    re.compile(
        r'<input[^>]*name=["\']authenticity_token["\'][^>]*value=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']authenticity_token["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]*name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'["\']authenticity_token["\']\s*[:=]\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
)


def _parse_authenticity_token(html: str) -> Optional[str]:
    """Extract Rails authenticity_token or meta csrf-token from HTML (never log value)."""
    if not html:
        return None
    for pat in _AUTH_TOKEN_PATTERNS:
        m = pat.search(html)
        if m and m.group(1):
            return m.group(1)
    return None


async def _fetch_csrf(cfg: Dict[str, Any]) -> str:
    """
    Fetch authenticity_token for the create form.

    Order:
      1. Cached SWIPESIMPLE_CSRF_TOKEN from env (bootstrap may refresh it)
      2. GET {base}{SWIPESIMPLE_NEW_INVOICE_PATH|/invoices/new} with Cookie — parse HTML

    Cookie header is always wired from SESSION / COOKIE_JAR. Never log token.
    """
    cached = (_secret_value(cfg, "_csrf") or "").strip()
    if cached:
        logger.info("[ss_invoice] using cached CSRF from env (value not logged)")
        return cached

    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    import httpx

    csrf_path = str(cfg.get("new_invoice_path") or new_invoice_path())
    url = urljoin(cfg["base_url"] + "/", csrf_path.lstrip("/"))
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": cookie,
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    logger.info("[ss_invoice] CSRF fetch GET path=%s (cookie present, not logged)", csrf_path)
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
    csrf_path = str(cfg.get("new_invoice_path") or new_invoice_path())
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "Origin": cfg["base_url"],
        "Referer": urljoin(cfg["base_url"] + "/", csrf_path.lstrip("/")),
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


def _extract_invoice_id_from_text(text: str, booking_number: str) -> Optional[str]:
    """Parse vendor invoice id near reference_id / booking # from HTML or JSON text."""
    if not text or not booking_number:
        return None
    booked = re.escape(booking_number)
    patterns = (
        rf'"id"\s*:\s*"([A-Za-z0-9_-]+)"[^}}]{{0,400}}"reference_id"\s*:\s*"{booked}"',
        rf'"reference_id"\s*:\s*"{booked}"[^}}]{{0,400}}"id"\s*:\s*"([A-Za-z0-9_-]+)"',
        rf'"invoice_id"\s*:\s*"([A-Za-z0-9_-]+)"[^}}]{{0,400}}"reference_id"\s*:\s*"{booked}"',
        rf'data-invoice-id=["\']([A-Za-z0-9_-]+)["\'][^>]{{0,200}}{booked}',
        rf'/invoices/([A-Za-z0-9_-]+)[^"\']*["\'][^>]*>\s*{booked}',
        rf'/api/v4/invoices/([A-Za-z0-9_-]+)',
    )
    skip = {"new", "edit", "index", "search", ""}
    for pat in patterns:
        mm = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if mm:
            cand = mm.group(1)
            if cand not in skip:
                return cand
    return None


def _extract_invoice_id_from_json(payload: Any, booking_number: str) -> Optional[str]:
    """Walk JSON list/dict for invoice whose reference_id matches booking #."""
    booked = str(booking_number or "").strip()
    if not booked:
        return None

    def _walk(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            ref = str(
                node.get("reference_id")
                or node.get("referenceId")
                or node.get("invoice_number")
                or node.get("number")
                or ""
            ).strip()
            inv_id = str(
                node.get("id")
                or node.get("invoice_id")
                or node.get("invoiceId")
                or ""
            ).strip()
            if ref == booked and inv_id and inv_id not in ("new", "edit"):
                return inv_id
            for v in node.values():
                found = _walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found:
                    return found
        return None

    return _walk(payload)


async def _resolve_invoice_id_after_create(
    *,
    booking_number: str,
    create_result: Dict[str, Any],
    cfg: Dict[str, Any],
) -> str:
    """
    Resolve vendor invoice_id after create 302 (often no JSON body / no id in Location).

    Strategy (fail-closed — never invent an id):
      1. Parse Location for /invoices/<id> if present (create uses follow_redirects=False)
      2. Parse create response body for id near booking #
      3. Prefer GET /api/v4/invoices?reference_id=<booking#> (JSON walk)
      4. Fallback GET /api/v4/invoices?q=… then HTML /invoices list parse

    If unresolved: raise invoice_id_unresolved_after_create_302.
    Caller MUST mark bond pending and MUST NOT create a duplicate draft.
    """
    location = str(create_result.get("location") or "")
    m = re.search(r"/invoices/([A-Za-z0-9_-]+)", location)
    if m and m.group(1) not in ("new", "edit", ""):
        logger.info("[ss_invoice] invoice_id from Location path")
        return m.group(1)

    body = str(create_result.get("body") or "")
    found = _extract_invoice_id_from_text(body, booking_number)
    if found:
        logger.info("[ss_invoice] invoice_id from create body pattern")
        return found

    _require_live()
    cookie = _cookie_header(cfg)
    if not cookie:
        raise SwipeSimpleInvoiceError("swipesimple_session_not_configured")

    import httpx

    headers = {
        "Accept": "application/json, text/html",
        "Cookie": cookie,
        "User-Agent": "ShamrockLeads-SwipeSimpleInvoice/1.0",
    }
    q = quote(booking_number, safe="")
    list_paths = (
        f"{_LIST_INVOICES_API}?reference_id={q}",
        f"{_LIST_INVOICES_API}?q={q}",
        f"{_LIST_INVOICES_API}?search={q}",
        "/invoices",
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=45.0) as client:
        for path in list_paths:
            url = urljoin(cfg["base_url"] + "/", path.lstrip("/"))
            logger.info(
                "[ss_invoice] resolve invoice_id via GET path=%s",
                path.split("?")[0],
            )
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:
                logger.warning(
                    "[ss_invoice] list fetch failed path=%s err=%s",
                    path.split("?")[0],
                    exc,
                )
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            raw = resp.text or ""
            if "json" in ctype or raw.lstrip().startswith(("{", "[")):
                try:
                    data = resp.json()
                    jid = _extract_invoice_id_from_json(data, booking_number)
                    if jid:
                        logger.info("[ss_invoice] invoice_id from API JSON reference_id match")
                        return jid
                except Exception:
                    pass
            tid = _extract_invoice_id_from_text(raw, booking_number)
            if tid:
                logger.info("[ss_invoice] invoice_id from list/search text")
                return tid

    raise SwipeSimpleInvoiceError(
        "invoice_id_unresolved_after_create_302 — "
        "create likely succeeded (draft may already exist for this booking #); "
        "do NOT create another invoice. Mark pending + resolve id via "
        "/api/v4/invoices?reference_id= or ops list scrape before copy_link."
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

    return {
        "payment_link": payment_link,
        "invoice_id": invoice_id,
        "invoice_number": booking_number,
        "amount": float(premium),
        "amount_cents": cents,
        "bond_id": bond_id,
        "create_status": create_result.get("status_code"),
    }


def _bond_update_filter(bond: Dict[str, Any], bond_id: str, booking_number: str) -> Dict[str, Any]:
    booking = _bond_booking(bond) or booking_number
    return {
        "$or": [
            {"booking_number": booking},
            {"bond_case_id": bond_id},
            {"bond_id": bond_id},
            {"swipesimple_invoice_number": booking_number},
        ]
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
        "swipesimple_invoice_unresolved": False,
        "payment_status": bond.get("payment_status") or "sent",
        "updated_at": now_iso,
    }
    filt = _bond_update_filter(bond, bond_id, booking_number)
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            await get_collection(coll_name).update_one(filt, {"$set": patch})
        except Exception as exc:
            logger.warning("[ss_invoice] persist on %s failed: %s", coll_name, exc)


async def _persist_unresolved_create(
    bond: Dict[str, Any],
    *,
    bond_id: str,
    booking_number: str,
    create_status: Any = None,
) -> None:
    """
    Fail-closed marker after create likely succeeded but invoice_id unresolved.
    Prevents duplicate draft create on retry until ops / API resolve the id.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    patch = {
        "swipesimple_invoice_number": booking_number,
        "swipesimple_invoice_bond_id": bond_id,
        "swipesimple_invoice_unresolved": True,
        "swipesimple_invoice_unresolved_at": now_iso,
        "swipesimple_invoice_create_status": create_status,
        "updated_at": now_iso,
    }
    filt = _bond_update_filter(bond, bond_id, booking_number)
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            await get_collection(coll_name).update_one(filt, {"$set": patch})
        except Exception as exc:
            logger.warning("[ss_invoice] unresolved persist on %s failed: %s", coll_name, exc)


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

    If a prior create left swipesimple_invoice_unresolved=True for this booking,
    fail-closed (do not create a duplicate draft).
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

    if bond.get("swipesimple_invoice_unresolved") is True and (
        str(bond.get("swipesimple_invoice_number") or "") == booking_number
        or str(bond.get("swipesimple_invoice_bond_id") or "") == bond_id
    ):
        raise SwipeSimpleInvoiceError(
            "invoice_create_pending_id_resolution — "
            "a prior create likely left a draft for this booking #; "
            "resolve swipesimple_invoice_id (API reference_id lookup) before retrying create"
        )

    vendor_id_existing = str(bond.get("swipesimple_invoice_id") or "").strip()
    if vendor_id_existing and not existing:
        cfg = load_swipesimple_session_config()
        payment_link = await _http_copy_link(invoice_id=vendor_id_existing, cfg=cfg)
        await _persist_invoice_fields(
            bond,
            bond_id=bond_id,
            booking_number=booking_number,
            payment_link=payment_link,
            invoice_id=vendor_id_existing,
        )
        return {
            "ok": True,
            "idempotent": False,
            "copy_link_only": True,
            "bond_id": bond_id,
            "booking_number": booking_number,
            "invoice_number": booking_number,
            "premium_amount": float(premium),
            "payment_link": payment_link,
            "swipesimple_invoice_id": vendor_id_existing,
        }

    cfg = load_swipesimple_session_config()

    try:
        http_result = await _share_invoice_http(
            booking_number=booking_number,
            premium=premium,
            bond_id=bond_id,
            bond=bond,
            cfg=cfg,
        )
    except SwipeSimpleInvoiceError as exc:
        if "invoice_id_unresolved_after_create_302" in str(exc):
            await _persist_unresolved_create(
                bond,
                bond_id=bond_id,
                booking_number=booking_number,
            )
        raise

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


def build_dispatch_payload(
    bond: Dict[str, Any],
    *,
    payment_link: str,
    booking_number: str,
    premium: Decimal,
    channel: Channel,
) -> Dict[str, Any]:
    """Build BlueBubbles / email payload without sending (safe for dry-run / tests)."""
    defendant = str(
        bond.get("defendant_name") or bond.get("Defendant_Name") or ""
    ).strip()
    phone = str(
        bond.get("indemnitor_phone")
        or bond.get("Indemnitor_Phone")
        or bond.get("defendant_phone")
        or ""
    ).strip()
    email = str(
        bond.get("indemnitor_email")
        or bond.get("Indemnitor_Email")
        or bond.get("defendant_email")
        or ""
    ).strip()
    body = (
        f"Shamrock Bail Bonds — premium payment for {defendant or 'your bond'} "
        f"(booking {booking_number}). Amount due: ${premium:,.2f}.\n"
        f"Pay Online via SwipeSimple:\n{payment_link}\n"
    )
    subject = (
        f"Shamrock Bail Bonds — Pay premium online "
        f"(booking {booking_number}, ${premium:,.2f})"
    )
    return {
        "channel": channel,
        "phone": phone,
        "email": email,
        "subject": subject,
        "body": body,
        "defendant_name": defendant,
        "has_recipient": bool(phone if channel == "imessage" else email),
    }


async def dispatch_invoice(
    bond_id: str,
    channel: Channel = "imessage",
) -> Dict[str, Any]:
    """
    Dispatch stored payment link via BlueBubbles (imessage) or email.

    Only runs after create_locked_invoice succeeded / link is stored.
    Default: dry-run — builds payload, logs intent, does NOT send.
    Live send requires SWIPESIMPLE_DISPATCH_LIVE=1 (separate from HTTP LIVE).
    Prefer web payment link; no dual SwipeSimple SMS.
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

    payload = build_dispatch_payload(
        bond,
        payment_link=payment_link,
        booking_number=booking_number,
        premium=premium,
        channel=channel,
    )

    live = dispatch_live_enabled()
    logger.info(
        "[ss_invoice] dispatch channel=%s bond_id=%s booking=%s "
        "has_phone=%s has_email=%s live=%s",
        channel,
        bond_id,
        booking_number,
        bool(payload["phone"]),
        bool(payload["email"]),
        live,
    )

    if not live:
        return {
            "ok": True,
            "sent": False,
            "dry_run": True,
            "stub": True,
            "channel": channel,
            "bond_id": bond_id,
            "booking_number": booking_number,
            "payment_link": payment_link,
            "premium_amount": float(premium),
            "has_recipient": payload["has_recipient"],
            "preview_body_chars": len(payload["body"]),
            "payload": {
                "channel": channel,
                "has_phone": bool(payload["phone"]),
                "has_email": bool(payload["email"]),
                "subject": payload["subject"],
                "body_chars": len(payload["body"]),
            },
            "message": "dispatch dry-run — set SWIPESIMPLE_DISPATCH_LIVE=1 to send",
        }

    if not payload["has_recipient"]:
        raise SwipeSimpleInvoiceError("dispatch_missing_recipient")

    sent = False
    if channel == "imessage":
        try:
            from dashboard.services.bb_client import (
                bb_send_accepted,
                normalize_bb_send_result,
                send_message_universal,
            )
        except ImportError as exc:
            raise SwipeSimpleInvoiceError(
                "dispatch_bb_client_import_failed — "
                "dashboard.services.bb_client is required for imessage channel "
                f"({exc})"
            ) from exc

        raw = await send_message_universal(payload["phone"], payload["body"])
        send_result = normalize_bb_send_result(raw)
        sent = bb_send_accepted(send_result)
    else:
        try:
            from dashboard.services.gmail_reader import GmailReaderService
        except ImportError as exc:
            raise SwipeSimpleInvoiceError(
                "dispatch_gmail_reader_import_failed — "
                "dashboard.services.gmail_reader is required for email channel "
                f"({exc})"
            ) from exc

        gmail = GmailReaderService()
        if not gmail.is_configured:
            raise SwipeSimpleInvoiceError("gmail_not_configured")
        send_result = gmail.send_email(
            to=payload["email"],
            subject=payload["subject"],
            body_text=payload["body"],
            body_html=payload["body"].replace("\n", "<br>\n"),
        )
        sent = bool(send_result.get("success"))

    return {
        "ok": True,
        "sent": bool(sent),
        "dry_run": False,
        "stub": False,
        "channel": channel,
        "bond_id": bond_id,
        "booking_number": booking_number,
        "payment_link": payment_link,
        "premium_amount": float(premium),
        "has_recipient": True,
        "send_result_ok": bool(sent),
        "message": "dispatch live send attempted" if sent else "dispatch live send failed",
    }


async def _find_bond_for_reconcile(booking: str, receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Match bond by booking # / invoice # / reference_id (fail-closed if none)."""
    ref = str(
        receipt.get("reference_id")
        or receipt.get("invoice_number")
        or receipt.get("swipesimple_invoice_number")
        or booking
        or ""
    ).strip()
    clauses = [
        {"booking_number": booking},
        {"swipesimple_invoice_number": booking},
        {"bond_case_id": booking},
    ]
    if ref and ref != booking:
        clauses.extend(
            [
                {"booking_number": ref},
                {"swipesimple_invoice_number": ref},
                {"bond_case_id": ref},
            ]
        )
    vendor_inv = str(receipt.get("invoice_id") or receipt.get("swipesimple_invoice_id") or "").strip()
    if vendor_inv:
        clauses.append({"swipesimple_invoice_id": vendor_inv})

    query = {"$or": clauses}
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            doc = await get_collection(coll_name).find_one(query)
            if doc:
                doc["_collection"] = coll_name
                return doc
        except Exception as exc:
            logger.warning("[ss_invoice] reconcile lookup %s failed: %s", coll_name, exc)
    return None


async def reconcile_payment(
    booking_number: Optional[str] = None,
    receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Match a paid SwipeSimple receipt → bond PAID + LedgerService entry.

    Idempotent if bond already PAID / payment_status=paid.
    Match on booking_number (= invoice # / reference_id), stored
    swipesimple_invoice_number, or vendor invoice_id when present.

    Safe without live SwipeSimple polling — callers pass a receipt dict from
    Gmail poller / webhook / CSV. Optional live poll remains out of scope
    unless a separately gated helper is added later.
    """
    receipt = receipt or {}
    booking = validate_booking_number(
        booking_number
        or receipt.get("booking_number")
        or receipt.get("reference_id")
        or receipt.get("invoice_number")
        or ""
    )

    bond = await _find_bond_for_reconcile(booking, receipt)
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
    paid_amount = float(amount) if amount is not None else (
        float(expected) if expected is not None else None
    )

    payment_update = {
        "payment_status": "paid",
        "premium_paid": True,
        "payment_received": True,
        "last_payment_amount": paid_amount,
        "last_payment_at": now_iso,
        "last_payment_status": "paid",
        "last_transaction_id": txn,
        "last_payment_source": "swipesimple_invoice_reconcile",
        "updated_at": now_iso,
    }

    filt = {
        "$or": [
            {"booking_number": booking},
            {"swipesimple_invoice_number": booking},
            {"bond_case_id": booking},
        ]
    }
    for coll_name in ("bond_cases", "active_bonds"):
        try:
            await get_collection(coll_name).update_one(filt, {"$set": payment_update})
        except Exception as exc:
            logger.warning("[ss_invoice] reconcile update %s failed: %s", coll_name, exc)

    ledger_txn = None
    try:
        from dashboard.services.ledger_service import LedgerService

        ledger_txn = await LedgerService.add_entry(
            {
                "booking_number": booking,
                "type": "payment",
                "category": "premium",
                "amount": paid_amount or 0,
                "actor": "SwipeSimpleInvoiceService",
                "notes": "swipesimple reconcile_payment (reference_id=booking #)",
                "stripe_swipe_ref": txn,
            }
        )
    except Exception as exc:
        logger.warning("[ss_invoice] ledger entry failed booking=%s: %s", booking, exc)

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
        "matched_collection": bond.get("_collection"),
    }


async def maybe_issue_share_invoice_for_bond(
    bond_id: str,
    *,
    channel: Channel = "imessage",
    dispatch: bool = True,
    source: str = "manual",
) -> Dict[str, Any]:
    """
    Thin integration entrypoint: create_locked_invoice then optional dispatch.

    Fail-closed + idempotent via create_locked_invoice.
    Call from:
      - Bond Desk / Paperwork Desk after paperwork-complete (recommended)
      - intake promote when SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE=1
      - Leads Ops manual / queue worker

    Does not invent premiums or links. Live HTTP still requires SWIPESIMPLE_LIVE;
    customer messages require SWIPESIMPLE_DISPATCH_LIVE.
    """
    bond_id = str(bond_id or "").strip()
    if not bond_id:
        raise SwipeSimpleInvoiceError("missing_bond_id")

    logger.info(
        "[ss_invoice] maybe_issue source=%s bond_id=%s dispatch=%s",
        source,
        bond_id,
        dispatch,
    )
    create_result = await create_locked_invoice(bond_id)
    out: Dict[str, Any] = {
        "ok": True,
        "source": source,
        "create": create_result,
        "dispatch": None,
    }
    if not dispatch:
        return out
    try:
        out["dispatch"] = await dispatch_invoice(bond_id, channel=channel)
    except Exception as exc:
        logger.warning(
            "[ss_invoice] dispatch after create failed source=%s bond_id=%s err=%s",
            source,
            bond_id,
            exc,
        )
        out["dispatch"] = {
            "ok": False,
            "sent": False,
            "error": str(exc),
        }
    return out



# ---------------------------------------------------------------------------
# Brendan $0.01 smoke (no BondCase, no dispatch, no Mongo writes)
# ---------------------------------------------------------------------------

SMOKE_AMOUNT_CENTS = 1  # locked $0.01 draft
_SMOKE_CUSTOMER_NAME_DEFAULT = "SMOKE TEST DO NOT PAY"


def default_smoke_reference_id() -> str:
    """Non-customer test reference_id: SMOKE-YYYYMMDD-HHMM (local box TZ)."""
    return datetime.now().strftime("SMOKE-%Y%m%d-%H%M")


async def smoke_create_one_cent_draft(
    *,
    reference_id: Optional[str] = None,
    customer_name: str = _SMOKE_CUSTOMER_NAME_DEFAULT,
    check_only: bool = False,
) -> Dict[str, Any]:
    """
    Brendan-approved $0.01 Share Invoice smoke: draft create + copy_link only.

    - Requires SWIPESIMPLE_LIVE=1 (and session cookie / jar in env).
    - Does NOT invent or touch BondCase / Mongo.
    - Does NOT dispatch BlueBubbles / email (even if SWIPESIMPLE_DISPATCH_LIVE=1).
    - Never logs or prints cookies, CSRF, or passwords.
    - reference_id defaults to SMOKE-YYYYMMDD-HHMM (override for retries).

    Ops: ``python scripts/swipesimple_smoke_create.py`` after SESSION is set.
    """
    ref = str(reference_id or default_smoke_reference_id()).strip()
    if not ref:
        raise SwipeSimpleInvoiceError("smoke_missing_reference_id")
    if not ref.upper().startswith("SMOKE"):
        raise SwipeSimpleInvoiceError(
            "smoke_reference_id_must_start_with_SMOKE — refuse non-smoke booking #"
        )

    name = str(customer_name or _SMOKE_CUSTOMER_NAME_DEFAULT).strip() or _SMOKE_CUSTOMER_NAME_DEFAULT
    cfg = load_swipesimple_session_config()
    gates = {
        "live_enabled": live_http_enabled(),
        "dispatch_live_enabled": dispatch_live_enabled(),
        "has_session": bool(cfg.get("has_session") or cfg.get("has_cookie_jar")),
        "has_csrf": bool(cfg.get("has_csrf")),
        "reference_id": ref,
        "amount_cents": SMOKE_AMOUNT_CENTS,
        "amount_dollars": "0.01",
    }
    logger.info(
        "[ss_invoice] smoke gates live=%s dispatch_live=%s has_session=%s "
        "has_csrf=%s reference_id=%s check_only=%s",
        gates["live_enabled"],
        gates["dispatch_live_enabled"],
        gates["has_session"],
        gates["has_csrf"],
        ref,
        check_only,
    )

    if check_only:
        return {
            "ok": True,
            "check_only": True,
            "ready": bool(gates["live_enabled"] and gates["has_session"]),
            "gates": gates,
            "message": (
                "smoke check-only — set SWIPESIMPLE_LIVE=1 and SWIPESIMPLE_SESSION "
                "(or COOKIE_JAR), then re-run without --check-only"
            ),
        }

    _require_live()
    if not (cfg.get("has_session") or cfg.get("has_cookie_jar")):
        raise SwipeSimpleInvoiceError(
            "swipesimple_session_not_configured — waiting on SWIPESIMPLE_SESSION "
            "(or SWIPESIMPLE_COOKIE_JAR) before $0.01 smoke"
        )

    # Synthetic customer only — never written to BondCase / Mongo.
    synthetic_bond = {
        "indemnitor_name": name,
        "indemnitor_email": "",
        "indemnitor_phone": "",
        "swipesimple_customer_id": "",
    }
    premium = Decimal("0.01")
    result = await _share_invoice_http(
        booking_number=ref,
        premium=premium,
        bond_id=f"smoke:{ref}",
        bond=synthetic_bond,
        cfg=cfg,
    )
    # Intentionally omit secrets; payment_link is the smoke artifact to verify.
    out = {
        "ok": True,
        "smoke": True,
        "dispatch": False,
        "bondcase_touched": False,
        "reference_id": ref,
        "invoice_number": ref,
        "invoice_id": result.get("invoice_id"),
        "payment_link": result.get("payment_link"),
        "amount_cents": SMOKE_AMOUNT_CENTS,
        "amount_dollars": 0.01,
        "create_status": result.get("create_status"),
        "message": (
            "smoke draft created + copy_link — verify in SwipeSimple UI; "
            "DISPATCH remains off for this script"
        ),
    }
    if dispatch_live_enabled():
        out["warning"] = (
            "SWIPESIMPLE_DISPATCH_LIVE is set but smoke script never dispatches"
        )
    logger.info(
        "[ss_invoice] smoke ok reference_id=%s invoice_id_len=%s has_link=%s",
        ref,
        len(str(out.get("invoice_id") or "")),
        bool(out.get("payment_link")),
    )
    return out


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
