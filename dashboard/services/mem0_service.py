"""
Mem0 long-term memory for Shannon / Agent Brain.

Shares the same Mem0 project as GAS voice Shannon (ElevenLabs_WebhookHandler.js):
  - Script property / env: MEMO_API_KEY (alias MEM0_API_KEY)
  - user_id: last 10 phone digits (GAS-compatible for cross-channel memory)
  - REST: https://api.mem0.ai/v1/memories/

Fail-open: missing key or API errors never break iMessage auto-reply.
Never log full phone numbers — only last-4 of normalized id.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MEM0_BASE = os.getenv("MEM0_API_BASE", "https://api.mem0.ai").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("MEM0_TIMEOUT_SECONDS", "4"))
DEFAULT_SEARCH_LIMIT = int(os.getenv("MEM0_SEARCH_LIMIT", "8"))

# Patterns to strip before shipping text to Mem0 (PII minimization)
_REDACT_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[CARD]"),
]


def _api_key() -> str:
    """Prefer GAS name MEMO_API_KEY; accept MEM0_API_KEY."""
    return (
        os.getenv("MEMO_API_KEY", "").strip()
        or os.getenv("MEM0_API_KEY", "").strip()
    )


def is_enabled() -> bool:
    """True when key present and not explicitly disabled."""
    flag = os.getenv("MEM0_ENABLED", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return bool(_api_key())
    return bool(_api_key())


def normalize_phone_digits(phone: str) -> str:
    """Last 10 digits — matches GAS saveMem0Memory_ / history lookup."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def phone_user_id(phone: str) -> str:
    """Mem0 user_id for this contact (GAS-compatible)."""
    return normalize_phone_digits(phone)


def _safe_user_log(user_id: str) -> str:
    if not user_id:
        return "none"
    return f"...{user_id[-4:]}" if len(user_id) >= 4 else "****"


def redact_text(text: str) -> str:
    out = text or ""
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def format_memory_block(facts: List[str]) -> str:
    """Bullet list for system-prompt injection."""
    clean = [f.strip() for f in facts if f and str(f).strip()]
    if not clean:
        return ""
    lines = "\n".join(f"- {c}" for c in clean[:DEFAULT_SEARCH_LIMIT])
    return (
        "KNOWN FACTS FROM PRIOR CONVERSATIONS / CALLS (may be incomplete; do not invent):\n"
        f"{lines}\n"
        "If a fact conflicts with what the person just said, trust the current message. "
        "Never quote internal system IDs, POA numbers, or payment credentials."
    )


def _auth_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }


def _parse_memory_list(payload: Any) -> List[str]:
    """Normalize Mem0 list / search response into fact strings."""
    if payload is None:
        return []
    items: List[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = (
            payload.get("results")
            or payload.get("memories")
            or payload.get("data")
            or []
        )
        if not items and payload.get("memory"):
            items = [payload]
    else:
        return []

    facts: List[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            facts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        text = (
            item.get("memory")
            or item.get("text")
            or item.get("data")
            or ""
        )
        if isinstance(text, dict):
            text = text.get("memory") or text.get("text") or ""
        if text and str(text).strip():
            facts.append(str(text).strip())
    return facts


def _search_sync(user_id: str, query: str, limit: int) -> List[str]:
    api_key = _api_key()
    if not api_key or not user_id:
        return []

    headers = _auth_headers(api_key)
    timeout = httpx.Timeout(DEFAULT_TIMEOUT)
    q = (query or "prior bail bond conversation").strip()[:500]

    try:
        with httpx.Client(timeout=timeout) as client:
            search_urls = [
                f"{MEM0_BASE}/v2/memories/search/",
                f"{MEM0_BASE}/v1/memories/search/",
            ]
            body = {
                "query": q,
                "filters": {"user_id": user_id},
                "limit": limit,
            }
            body_legacy = {
                "query": q,
                "user_id": user_id,
                "limit": limit,
            }
            for url in search_urls:
                for payload in (body, body_legacy):
                    try:
                        r = client.post(url, headers=headers, json=payload)
                        if r.status_code in (200, 201):
                            facts = _parse_memory_list(r.json())
                            if facts:
                                return facts[:limit]
                    except Exception:
                        continue

            # List memories for user (GAS history path)
            r = client.get(
                f"{MEM0_BASE}/v1/memories/",
                headers=headers,
                params={"user_id": user_id, "limit": limit},
            )
            if r.status_code == 200:
                return _parse_memory_list(r.json())[:limit]
            logger.debug(
                "mem0 list status=%s user=%s",
                r.status_code,
                _safe_user_log(user_id),
            )
    except Exception as e:
        logger.warning("mem0 search failed (non-fatal): %s", type(e).__name__)
    return []


def _add_sync(
    user_id: str,
    messages: List[Dict[str, str]],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    api_key = _api_key()
    if not api_key or not user_id or not messages:
        return False

    safe_messages = []
    for m in messages:
        role = m.get("role") or "user"
        content = redact_text(str(m.get("content") or "")).strip()
        if not content:
            continue
        if role not in ("user", "assistant", "system"):
            role = "user"
        safe_messages.append({"role": role, "content": content[:1500]})
    if not safe_messages:
        return False

    meta = {
        "category": "imessage_outreach",
        "source": "shamrock-leads",
        "agent_involved": "shannon",
        **(metadata or {}),
    }
    meta = {k: v for k, v in meta.items() if v not in (None, "")}

    payload = {
        "messages": safe_messages,
        "user_id": user_id,
        "metadata": meta,
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(DEFAULT_TIMEOUT)) as client:
            r = client.post(
                f"{MEM0_BASE}/v1/memories/",
                headers=_auth_headers(api_key),
                json=payload,
            )
            if r.status_code in (200, 201, 204):
                logger.info(
                    "mem0 stored user=%s status=%s msgs=%d",
                    _safe_user_log(user_id),
                    r.status_code,
                    len(safe_messages),
                )
                return True
            logger.debug(
                "mem0 add status=%s user=%s",
                r.status_code,
                _safe_user_log(user_id),
            )
    except Exception as e:
        logger.warning("mem0 add failed (non-fatal): %s", type(e).__name__)
    return False


async def search_facts(
    phone: str,
    query: str,
    *,
    limit: Optional[int] = None,
) -> List[str]:
    """Return plain fact strings for this phone; [] if disabled/error."""
    if not is_enabled():
        return []
    user_id = phone_user_id(phone)
    if len(user_id) < 7:
        return []
    lim = limit if limit is not None else DEFAULT_SEARCH_LIMIT
    try:
        facts = await asyncio.to_thread(_search_sync, user_id, query, lim)
        if facts:
            logger.info(
                "mem0 search hits=%d user=%s",
                len(facts),
                _safe_user_log(user_id),
            )
        return facts
    except Exception as e:
        logger.warning("mem0 search_facts error: %s", type(e).__name__)
        return []


async def remember_exchange(
    phone: str,
    messages: List[Dict[str, str]],
    *,
    booking_number: str = "",
    county: str = "",
    intent: str = "",
    channel: str = "imessage",
) -> bool:
    """Persist recent turns as Mem0 memories. Never raises."""
    if not is_enabled():
        return False
    store_flag = os.getenv("MEM0_STORE_ON_INBOUND", "true").strip().lower()
    if store_flag in ("0", "false", "no", "off"):
        return False
    user_id = phone_user_id(phone)
    if len(user_id) < 7:
        return False
    trimmed = list(messages or [])[-8:]
    metadata = {
        "booking_number": booking_number or "",
        "county": county or "",
        "intent": intent or "",
        "channel": channel,
        "category": "imessage_outreach" if channel == "imessage" else channel,
    }
    try:
        return await asyncio.to_thread(_add_sync, user_id, trimmed, metadata)
    except Exception as e:
        logger.warning("mem0 remember_exchange error: %s", type(e).__name__)
        return False


async def get_memory_block(phone: str, query: str) -> str:
    """Convenience: search + format for prompt injection."""
    facts = await search_facts(phone, query)
    return format_memory_block(facts)


def status_snapshot() -> Dict[str, Any]:
    """Non-secret status for health endpoints. Never include key fragments."""
    key = _api_key()
    return {
        "enabled": is_enabled(),
        "configured": bool(key),
        "api_base": MEM0_BASE,
        "search_limit": DEFAULT_SEARCH_LIMIT,
        "timeout_seconds": DEFAULT_TIMEOUT,
        "user_id_scheme": "last10_digits_gas_compatible",
        "env_keys": ["MEMO_API_KEY", "MEM0_API_KEY"],
    }
