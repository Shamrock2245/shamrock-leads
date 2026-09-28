"""
SwipeSimple merchant-session keep-alive (read-only) + expiry alert.

Probes GET /api/v4/customers?name=Brendan with the existing session cookie.
Does not create invoices, dispatch payment links, or run Playwright auto-login.

Opt-in (both required; production stays quiet until Brendan / Leads Ops enable):
  SWIPESIMPLE_SESSION_KEEPALIVE=1
  automation_config.swipesimple_session_keepalive.enabled  (cron default_enabled=False)

Never log cookie values, CSRF tokens, or exception text that might carry them.
SWIPESIMPLE_LIVE and SWIPESIMPLE_DISPATCH_LIVE are not read or set here.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

logger = logging.getLogger("shamrock.swipesimple_session_keepalive")

PROBE_PATH = "/api/v4/customers"
PROBE_NAME = "Brendan"
HEALTH_COLLECTION = "swipesimple_session_health"
HEALTH_DOC_ID = "merchant_session"
ALERT_COOLDOWN = timedelta(hours=6)
DEFAULT_ALERT_TO = "2399550178"  # office text line 239-955-0178
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ALERT_REASONS = frozenset({
    "unauthorized",
    "redirect_login",
    "redirect",
    "html_login",
    "non_json",
})
_LOGIN_LOCATION_MARKERS = ("login", "sign_in", "signin", "sign-in")
ALERT_TEXT = (
    "SwipeSimple login expired on ShamrockLeads. "
    "Paste a fresh Cookie header as SWIPESIMPLE_SESSION and restart the dashboard. "
    "Live invoicing may fail until then."
)

_once_flags: set[str] = set()
_HEALTH_UNAVAILABLE = object()


def keepalive_enabled() -> bool:
    """True only when SWIPESIMPLE_SESSION_KEEPALIVE is an explicit truthy value."""
    raw = (os.getenv("SWIPESIMPLE_SESSION_KEEPALIVE") or "").strip().lower()
    return raw in _TRUTHY


def alert_phone() -> str:
    """Recipient for the expiry text. Env override or the 0178 office line."""
    raw = (os.getenv("SWIPESIMPLE_SESSION_ALERT_TO") or "").strip()
    return raw or DEFAULT_ALERT_TO


def _log_once(flag: str, level: int, msg: str) -> None:
    if flag in _once_flags:
        return
    _once_flags.add(flag)
    logger.log(level, msg)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _header(headers: Any, name: str) -> str:
    if not headers:
        return ""
    try:
        items = headers.items()
    except Exception:
        return ""
    for key, value in items:
        if str(key).lower() == name.lower():
            return str(value or "")
    return ""


def _looks_like_html(content_type: str, body_text: str) -> bool:
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/xhtml" in ct:
        return True
    sample = (body_text or "").lstrip()[:240].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html")


def _looks_like_login_page(content_type: str, body_text: str) -> bool:
    """HTML from this JSON probe is a login wall or another non-API page."""
    if _looks_like_html(content_type, body_text):
        return True
    sample = (body_text or "")[:800].lower()
    return "<form" in sample and any(marker in sample for marker in _LOGIN_LOCATION_MARKERS)


def _public_probe(ok: bool, status: Optional[int], reason: str, cookie: str = "") -> Dict[str, Any]:
    """Result contract: ok / status / reason. Never includes cookie material."""
    token = str(reason or "unknown")
    if cookie and len(cookie) >= 12 and cookie in token:
        token = "redacted"
    if len(token) > 64:
        token = "redacted"
    safe_status: Optional[int]
    if isinstance(status, int):
        safe_status = status
    else:
        safe_status = None
    return {"ok": bool(ok), "status": safe_status, "reason": token}


def classify_probe_response(
    status_code: int,
    *,
    content_type: str = "",
    body_text: str = "",
    location: str = "",
    json_value: Any = None,
    json_ok: bool = False,
) -> Dict[str, Any]:
    """
    Map a probe response to ok/status/reason.

    Success is HTTP 200 plus a JSON object or array.
    401/403, login redirects, HTML login pages, and other non-JSON 200s are failures.
    Body text is inspected locally and is not returned.
    """
    status = int(status_code)
    if status in (401, 403):
        return _public_probe(False, status, "unauthorized")

    if 300 <= status < 400:
        loc = (location or "").lower()
        if any(marker in loc for marker in _LOGIN_LOCATION_MARKERS):
            return _public_probe(False, status, "redirect_login")
        return _public_probe(False, status, "redirect")

    if status == 200 and json_ok and isinstance(json_value, (dict, list)):
        return _public_probe(True, status, "ok")

    if status == 200:
        if _looks_like_login_page(content_type, body_text):
            return _public_probe(False, status, "html_login")
        return _public_probe(False, status, "non_json")

    if status == 429 or status >= 500:
        return _public_probe(False, status, f"http_{status}")

    return _public_probe(False, status, f"http_{status}")


def _probe_url(base_url: str) -> str:
    base = (base_url or "https://swipesimple.com").strip().rstrip("/") or "https://swipesimple.com"
    query = urlencode({"name": PROBE_NAME})
    return f"{base}{PROBE_PATH}?{query}"


async def probe_session() -> Dict[str, Any]:
    """
    Read-only session probe.

    Returns {ok, status, reason}. Cookie values are never included.
    Missing/empty session config returns ok=False, reason=missing_session
    and does not open a connection.
    """
    from dashboard.services.swipesimple_invoice_service import (
        _cookie_header,
        load_swipesimple_session_config,
    )

    cfg = load_swipesimple_session_config()
    cookie = _cookie_header(cfg)
    if not cookie:
        return _public_probe(False, None, "missing_session")

    url = _probe_url(str(cfg.get("base_url") or ""))
    headers = {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
        "User-Agent": "ShamrockLeads-SwipeSimpleKeepalive/1.0",
    }
    try:
        import httpx

        async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
        json_value = None
        json_ok = False
        if getattr(resp, "status_code", None) == 200:
            try:
                json_value = resp.json()
                json_ok = True
            except Exception:
                json_ok = False
        content_type = _header(getattr(resp, "headers", None), "content-type")
        location = _header(getattr(resp, "headers", None), "location")
        body_text = ""
        if not json_ok:
            body_text = str(getattr(resp, "text", "") or "")[:800]
        return classify_probe_response(
            int(resp.status_code),
            content_type=content_type,
            body_text=body_text,
            location=location,
            json_value=json_value,
            json_ok=json_ok,
        )
    except Exception as exc:
        logger.warning(
            "[ss_session] probe failed error_type=%s",
            type(exc).__name__,
        )
        return _public_probe(False, None, "probe_error", cookie=cookie)


def _collection(db: Any):
    if db is None:
        from dashboard.extensions import get_db
        db = get_db()
    return db[HEALTH_COLLECTION]


def _safe_health_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Drop anything that could hold a cookie, token, or response body."""
    blocked = ("cookie", "csrf", "session", "authorization", "token", "password")
    safe: Dict[str, Any] = {}
    for key, value in fields.items():
        lowered = str(key).lower()
        if any(part in lowered for part in blocked):
            continue
        if isinstance(value, str) and len(value) > 80:
            continue
        if isinstance(value, (str, int, bool)) or value is None:
            safe[key] = value
    return safe


async def _load_health(db: Any) -> Any:
    try:
        return await _collection(db).find_one({"_id": HEALTH_DOC_ID})
    except Exception as exc:
        logger.warning("[ss_session] health read failed error_type=%s", type(exc).__name__)
        return _HEALTH_UNAVAILABLE


async def _touch_health(db: Any, fields: Dict[str, Any]) -> None:
    payload = _safe_health_fields(fields)
    if not payload:
        return
    try:
        await _collection(db).update_one(
            {"_id": HEALTH_DOC_ID},
            {"$set": payload},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[ss_session] health write failed error_type=%s", type(exc).__name__)


def alert_due(health: Any, now: datetime) -> bool:
    """
    Alert on a fresh expiry, then at most once per 6 hours.

    A successful probe after the last alert clears the cooldown so the next
    failure alerts again.
    """
    if not health:
        return True
    last_alert = _parse_dt(health.get("last_alert_at"))
    if last_alert is None:
        return True
    last_ok = _parse_dt(health.get("last_ok_at"))
    if last_ok is not None and last_ok > last_alert:
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now - last_alert >= ALERT_COOLDOWN


async def _send_expiry_alert(phone: str) -> Dict[str, Any]:
    """BlueBubbles text to Brendan; Telegram staff alert is best-effort."""
    accepted = False
    try:
        from dashboard.services.bb_client import bb_send_accepted, send_message_universal

        bb_result = await send_message_universal(
            phone,
            ALERT_TEXT,
            purpose="swipesimple_session_alert",
        )
        accepted = bb_send_accepted(bb_result)
    except Exception as exc:
        logger.warning("[ss_session] bb alert failed error_type=%s", type(exc).__name__)
        accepted = False

    # Telegram is a secondary copy of the same alert. Skip it when BlueBubbles
    # did not accept, so a down bridge does not page staff every cycle.
    telegram = "skipped"
    if accepted:
        try:
            from dashboard.services.telegram_service import get_telegram_service

            tg_result = await get_telegram_service().send_staff_alert(ALERT_TEXT)
            telegram = "sent" if isinstance(tg_result, dict) and tg_result.get("success") else "not_sent"
        except Exception as exc:
            logger.warning("[ss_session] telegram alert failed error_type=%s", type(exc).__name__)
            telegram = "error"
    return {"bb_accepted": accepted, "telegram": telegram}


async def run_session_keepalive(*, db: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    One keep-alive cycle: probe, record health, alert on expiry with cooldown.

    Soft-fails. Never raises for probe, Mongo, or alert errors.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    if not keepalive_enabled():
        _log_once(
            "disabled",
            logging.INFO,
            "[ss_session] keep-alive disabled (SWIPESIMPLE_SESSION_KEEPALIVE unset); no probe",
        )
        return {
            "ok": False,
            "status": None,
            "reason": "keepalive_disabled",
            "alerted": False,
            "alert_suppressed": False,
        }

    try:
        probe = await probe_session()
    except Exception as exc:
        logger.warning("[ss_session] probe raised error_type=%s", type(exc).__name__)
        probe = _public_probe(False, None, "probe_error")

    reason = str(probe.get("reason") or "unknown")
    result: Dict[str, Any] = {
        "ok": bool(probe.get("ok")),
        "status": probe.get("status") if isinstance(probe.get("status"), int) else None,
        "reason": reason,
        "alerted": False,
        "alert_suppressed": False,
    }

    if reason == "missing_session":
        _log_once(
            "missing_session",
            logging.INFO,
            "[ss_session] session not configured reason=missing_session; alert skipped",
        )
        return result

    if result["ok"]:
        await _touch_health(db, {
            "last_ok_at": _iso(moment),
            "last_status": result["status"],
            "last_reason": "ok",
            "updated_at": _iso(moment),
        })
        logger.info(
            "[ss_session] probe ok status=%s reason=%s",
            result["status"],
            result["reason"],
        )
        return result

    health = await _load_health(db)
    health_down = health is _HEALTH_UNAVAILABLE
    alertable = reason in _ALERT_REASONS and not health_down
    due = alertable and alert_due(None if health_down else health, moment)
    if alertable and not due:
        result["alert_suppressed"] = True
    if health_down and reason in _ALERT_REASONS:
        result["alert_suppressed"] = True
        logger.warning("[ss_session] expiry alert skipped; health doc unreadable reason=%s", reason)

    fields: Dict[str, Any] = {
        "last_fail_at": _iso(moment),
        "last_reason": reason,
        "updated_at": _iso(moment),
    }
    if isinstance(result["status"], int):
        fields["last_status"] = result["status"]
    if due:
        send = await _send_expiry_alert(alert_phone())
        result["alerted"] = bool(send.get("bb_accepted"))
        if result["alerted"]:
            fields["last_alert_at"] = _iso(moment)
        else:
            logger.warning("[ss_session] expiry alert not accepted reason=%s", reason)

    await _touch_health(db, fields)
    logger.info(
        "[ss_session] probe not ok status=%s reason=%s alerted=%s suppressed=%s",
        result["status"],
        result["reason"],
        result["alerted"],
        result["alert_suppressed"],
    )
    return result
