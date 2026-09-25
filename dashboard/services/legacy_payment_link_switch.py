"""Owner switch for the legacy static SwipeSimple payment-link AUTO send.

One switch, three automatic call sites (all go through
``packet_payment_link_service.maybe_send_packet_payment_link``):

  * DocuSeal completion  (shared handler, ``docuseal_completion``)
  * intake promote        (``dashboard/routers/intake.py`` — step 7a)
  * packet finalize       (``dashboard/routers/paperwork.py`` — finalize)

Env ``DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK`` — DEFAULT OFF.

  value                                        completion path            intake promote / packet finalize
  -------------------------------------------  -------------------------  --------------------------------
  unset / "" / 0 / false / no / off /          off                        off
  disabled / none / ANY unrecognized value
  1 / true / yes / on / enabled / webhook      webhook-received only      enabled
  all / both                                   webhook + poller           enabled

i.e. for intake promote and packet finalize, ANY enabled value (webhook-style
or all) enables them; the webhook-vs-poller distinction only exists for the
completion path. Unknown values fail closed (off).

Even when enabled, an automatic send only happens when a STAFF-CONFIRMED
premium is present (see ``packet_payment_link_service.confirmed_premium``);
otherwise the send is skipped with ``reason=premium_unconfirmed``.

The staff "Send payment link" endpoint (POST /api/paperwork/payment/swipesimple-link)
is an explicit staff action and is NOT governed by this switch.

This module has no dependencies so any caller can import it cheaply.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# OWNER SWITCH — DEFAULT OFF (owner decision 2026-09-25). One-line revert:
# change this constant (the env var still wins when set).
LEGACY_PAYMENT_LINK_DEFAULT = "off"
# ─────────────────────────────────────────────────────────────────────────────
LEGACY_PAYMENT_LINK_ENV = "DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK"
LEGACY_MODES = ("off", "webhook", "all")

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled", "webhook"})
_ALL = frozenset({"all", "both"})
_OFF = frozenset({"", "0", "false", "no", "off", "disabled", "none"})


def legacy_payment_link_mode() -> str:
    """'off' (default) | 'webhook' | 'all'. Anything unrecognized → 'off' (fail closed)."""
    raw = (os.getenv(LEGACY_PAYMENT_LINK_ENV) or LEGACY_PAYMENT_LINK_DEFAULT or "").strip().lower()
    if raw in _ALL:
        return "all"
    if raw in _TRUTHY:
        return "webhook"
    if raw not in _OFF:
        logger.warning(
            "[payment_link_switch] unrecognized %s value — treating as off",
            LEGACY_PAYMENT_LINK_ENV,
        )
    return "off"


def legacy_payment_link_enabled() -> bool:
    """True for ANY enabled value (webhook-style or all). Used by intake promote,
    packet finalize, and as the service-level guard in maybe_send_packet_payment_link."""
    return legacy_payment_link_mode() != "off"
