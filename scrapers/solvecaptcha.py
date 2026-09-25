"""Shared SolveCaptcha Cloudflare Turnstile helper (owner-approved counties only).

Used where Brendan explicitly approved buying a Turnstile token for an official
public roster: Broward (2026-09-23, ``action=arrest_search``) and Lake
(2026-09-25). Plain HTTPS only — no proxy, Obscura, or stealth browser.

The API key comes from ``SOLVECAPTCHA_KEY`` and is never logged: exceptions are
logged by type name only (their text can include request URLs with the key).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SUBMIT_URL = "https://api.solvecaptcha.com/in.php"
RESULT_URL = "https://api.solvecaptcha.com/res.php"
ENV_KEY = "SOLVECAPTCHA_KEY"


def solvecaptcha_key() -> str:
    return os.getenv(ENV_KEY, "").strip()


def solve_turnstile(
    api_key: str,
    *,
    sitekey: str,
    pageurl: str,
    action: Optional[str] = None,
    http: Any = None,
    polls: int = 40,
    poll_interval: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    log_prefix: str = "[SolveCaptcha]",
) -> Optional[str]:
    """Return a Turnstile token for ``sitekey`` on ``pageurl``, or ``None``.

    ``http`` is any module/session exposing ``post(url, data=, timeout=)`` and
    ``get(url, params=, timeout=)`` returning objects with ``.json()``
    (``requests`` by default; Broward passes ``curl_cffi.requests``).
    ``action`` must match the widget's ``data-action`` when it declares one
    (Broward: ``arrest_search``) or the site rejects the token.
    """
    if not api_key:
        logger.warning("%s %s not set — cannot solve Turnstile", log_prefix, ENV_KEY)
        return None
    if http is None:
        import requests as http  # noqa: PLW0621 - default transport

    data = {
        "key": api_key,
        "method": "turnstile",
        "sitekey": sitekey,
        "pageurl": pageurl,
        "json": "1",
    }
    if action:
        data["action"] = action

    logger.info("%s Solving Turnstile via SolveCaptcha...", log_prefix)
    try:
        submit = http.post(SUBMIT_URL, data=data, timeout=30).json()
    except Exception as e:  # never log str(e): may contain the key
        logger.error("%s SolveCaptcha submit exception: %s", log_prefix, type(e).__name__)
        return None
    if submit.get("status") != 1:
        logger.error("%s SolveCaptcha submit failed: %s", log_prefix, submit.get("request"))
        return None
    task_id = submit.get("request")
    logger.info("%s SolveCaptcha task: %s", log_prefix, task_id)

    for _ in range(polls):
        sleep(poll_interval)
        try:
            result = http.get(
                RESULT_URL,
                params={"key": api_key, "action": "get", "id": task_id, "json": "1"},
                timeout=15,
            ).json()
        except Exception as e:
            logger.warning("%s SolveCaptcha poll error: %s", log_prefix, type(e).__name__)
            continue
        if result.get("status") == 1:
            logger.info("%s Turnstile solved", log_prefix)
            return result.get("request")
        if "CAPCHA_NOT_READY" in str(result.get("request", "")):
            continue
        logger.error("%s SolveCaptcha error: %s", log_prefix, result.get("request"))
        return None
    logger.error("%s SolveCaptcha timeout", log_prefix)
    return None
