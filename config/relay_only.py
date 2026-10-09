"""Counties that run only on the Leads Ops home relay (never on the VPS).

Manatee and Charlotte (FL) sit behind Cloudflare on Revize CMS and run only from
the relay's own US residential exit, with stock Playwright, no proxy and no
stealth (Manatee #121, Charlotte 2026-10-07). Pinellas (FL) is a Blazor Server
app with no plain-HTTP listing; owner exception (Brendan 2026-10-08 1:38 PM
ET): a non-stealth stock Playwright Chromium with an honest User-Agent, on the
residential relay only (docs/recon/FL_PINELLAS_RELAY_ONLY_2026-10-08.md).

The VPS/Hetzner scheduler keeps them registered, so ``python main.py Manatee`` still resolves, but it never puts
them on an interval job. A dashboard run-now or custody-recheck trigger for one
of them is marked ``relay_only`` on the VPS and is not run there.

A relay-only county that is ``fail_closed`` (``SOURCE_CONTRACT_VALIDATED =
False``) is skipped by ``--relay-only`` with no source request: Manatee and
Charlotte since 2026-10-09 (Cloudflare challenge from every exit tried).

On the relay, Leads Ops runs them with ``python main.py --relay-only`` (each
relay-only county once, then exit) or ``python main.py Manatee``. See
docs/ops/REVIZE_RELAY_RUN.md and docs/ops/PINELLAS_RELAY_RUN.md.
"""
from __future__ import annotations

from typing import Any

RELAY_ONLY_LABELS = frozenset({"Manatee (FL)", "Charlotte (FL)", "Pinellas (FL)"})


def label_for(scraper: Any) -> str:
    label = getattr(scraper, "county_label", None)
    if isinstance(label, str) and label:
        return label
    state = (getattr(scraper, "state", None) or "FL").upper()
    return f"{getattr(scraper, 'county', '')} ({state})"


def is_relay_only(scraper: Any) -> bool:
    return label_for(scraper) in RELAY_ONLY_LABELS
