"""Per-county roster fetch strategy.

Owner directive (Brendan, 2026-11-13): "Yes use a solver and a stealth browser
if you'd like. I acknowledge that every county will be different and our tech
stack should be adaptable to each county." He also asked that Manatee jail and
Sarasota jail run through his Mac when that relay is up.

That exception is only those two jails. Hillsborough never gets a solver.
Every other county keeps a plain plan and is not fetched by this module.

Order for Manatee jail and Sarasota jail, once a later PR allows a fetch:
1. Owner Mac residential relay (same idea as Pinellas: the Mac's own exit).
2. Stealth browser plus the existing SolveCaptcha helper, only if the relay
   is unreachable and ``SOLVECAPTCHA_KEY`` is set.
3. Fail closed: emit nothing and record a reason code. No names and no keys.

``SOURCE_CONTRACT_VALIDATED`` stays False on both scrapers, so
``BaseScraper.run`` still returns before any source request.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

MAC_RELAY = "mac_relay"
STEALTH_SOLVER = "stealth_solver"
FAIL_CLOSED = "fail_closed"
PLAIN = "plain"

RELAY_URL_ENV = "SHAMROCK_MAC_RELAY_URL"
RELAY_TOKEN_ENV = "SHAMROCK_MAC_RELAY_TOKEN"
SOLVER_ENV = "SOLVECAPTCHA_KEY"

CHALLENGE_ORDER = (MAC_RELAY, STEALTH_SOLVER, FAIL_CLOSED)

MANATEE_BOOKINGS_URL = "https://manatee-sheriff.revize.com/bookings"
SARASOTA_LISTING_URL = "https://cms.revize.com/revize/apps/sarasota/"

# The Mac relay may fetch only these two pages. It is not an open proxy.
RELAY_ALLOWLIST: Dict[Tuple[str, str], str] = {
    ("Manatee", "FL"): MANATEE_BOOKINGS_URL,
    ("Sarasota", "FL"): SARASOTA_LISTING_URL,
}

_CHALLENGE_MARKERS = (
    "cf-mitigated",
    "just a moment",
    "cf-chl",
    "challenge-platform",
    "sorry, you have been blocked",
)


@dataclass(frozen=True)
class AccessPlan:
    county: str
    state: str
    order: Tuple[str, ...]
    target_url: str = ""
    allow_solver: bool = False


MANATEE_JAIL = AccessPlan(
    "Manatee", "FL", CHALLENGE_ORDER, MANATEE_BOOKINGS_URL, allow_solver=True,
)
SARASOTA_JAIL = AccessPlan(
    "Sarasota", "FL", CHALLENGE_ORDER, SARASOTA_LISTING_URL, allow_solver=True,
)


def plan_for(county: str, state: str = "FL") -> AccessPlan:
    """The fetch plan for one county. Unknown counties stay plain."""
    name = " ".join(str(county or "").split())
    st = " ".join(str(state or "FL").split()).upper() or "FL"
    key = (name.casefold(), st)
    if key == ("hillsborough", "FL"):
        return AccessPlan("Hillsborough", "FL", (PLAIN,), allow_solver=False)
    if key == ("manatee", "FL"):
        return MANATEE_JAIL
    if key == ("sarasota", "FL"):
        return SARASOTA_JAIL
    return AccessPlan(name or county, st, (PLAIN,), allow_solver=False)


@dataclass
class Attempt:
    strategy: str
    ok: bool
    reason: str
    body: str = ""


@dataclass
class AccessResult:
    ok: bool
    strategy: str
    reason: str
    body: str = ""
    attempts: List[Attempt] = field(default_factory=list)


RelayGet = Callable[[str, str, str, str, str], Tuple[int, str]]
StealthFetch = Callable[[str, str], Tuple[int, str]]


def looks_like_challenge(body: str, status: int = 200) -> bool:
    """True for a Cloudflare challenge page. The body is not logged."""
    if status in (403, 429, 503):
        return True
    low = (body or "")[:20000].lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _mac_relay(plan: AccessPlan, env: Mapping[str, str], relay_get: RelayGet) -> Attempt:
    url = str(env.get(RELAY_URL_ENV) or "").strip()
    token = str(env.get(RELAY_TOKEN_ENV) or "").strip()
    if not url or not token:
        return Attempt(MAC_RELAY, False, "relay_not_configured")
    try:
        status, body = relay_get(url, token, plan.county, plan.state, plan.target_url)
    except Exception as exc:
        logger.warning("[access] mac relay unreachable error_type=%s", type(exc).__name__)
        return Attempt(MAC_RELAY, False, "relay_unreachable")
    if status != 200 or looks_like_challenge(body, status):
        return Attempt(MAC_RELAY, False, "relay_blocked" if status == 200 or status == 403 else "relay_rejected")
    return Attempt(MAC_RELAY, True, "relay_ok", body)


def _stealth(plan: AccessPlan, env: Mapping[str, str], stealth_fetch: StealthFetch) -> Attempt:
    if not plan.allow_solver:
        return Attempt(STEALTH_SOLVER, False, "solver_not_allowed")
    key = str(env.get(SOLVER_ENV) or "").strip()
    if not key:
        logger.warning("[access] stealth solver skipped code=solver_missing county=%s", plan.county)
        return Attempt(STEALTH_SOLVER, False, "solver_missing")
    try:
        status, body = stealth_fetch(plan.target_url, key)
    except Exception as exc:
        logger.warning("[access] stealth browser unavailable error_type=%s", type(exc).__name__)
        return Attempt(STEALTH_SOLVER, False, "stealth_unavailable")
    if status != 200 or looks_like_challenge(body, status):
        return Attempt(STEALTH_SOLVER, False, "stealth_blocked")
    return Attempt(STEALTH_SOLVER, True, "stealth_ok", body)


def run_access(
    plan: AccessPlan,
    *,
    env: Mapping[str, str],
    relay_get: RelayGet,
    stealth_fetch: StealthFetch,
) -> AccessResult:
    """Try the plan in order. A failed step never raises and never logs a body.

    ``relay_get(url, token, county, state, target_url) -> (status, body)``.
    ``stealth_fetch(target_url, solver_key) -> (status, body)``.
    The key and token are not logged.
    """
    attempts: List[Attempt] = []
    for step in plan.order:
        if step == MAC_RELAY:
            attempt = _mac_relay(plan, env, relay_get)
        elif step == STEALTH_SOLVER:
            attempt = _stealth(plan, env, stealth_fetch)
        elif step == PLAIN:
            attempt = Attempt(PLAIN, False, "plain_fetcher_not_configured")
        else:
            attempt = Attempt(FAIL_CLOSED, False, "all_paths_failed")
        attempts.append(attempt)
        logger.info(
            "[access] county=%s state=%s strategy=%s ok=%s reason=%s",
            plan.county, plan.state, attempt.strategy, attempt.ok, attempt.reason,
        )
        if attempt.ok:
            return AccessResult(True, attempt.strategy, attempt.reason, attempt.body, attempts)
        if attempt.strategy == FAIL_CLOSED:
            break
    return AccessResult(False, FAIL_CLOSED, "all_paths_failed", "", attempts)


def relay_target(county: str, state: str) -> Optional[str]:
    """The only URL the Mac relay may fetch for this county, or None."""
    name = " ".join(str(county or "").split())
    st = " ".join(str(state or "").split()).upper()
    return RELAY_ALLOWLIST.get((name, st))
