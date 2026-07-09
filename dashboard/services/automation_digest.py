"""
Shared Slack digests for revenue / ops automations.

Keeps PII out of Slack (last-4 only) and fails soft if webhooks missing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def post_slack(text: str, webhook_env: str = "SLACK_WEBHOOK_LEADS") -> bool:
    """Post plain text to a Slack incoming webhook. Returns True on success."""
    url = (os.getenv(webhook_env) or "").strip()
    if not url:
        logger.debug("[automation-digest] %s not set — skip Slack", webhook_env)
        return False
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.post(url, json={"text": text}, timeout=8)
            return r.status_code < 300
    except Exception as exc:
        logger.warning("[automation-digest] Slack post failed: %s", exc)
        return False


def _last4(phone: Optional[str]) -> str:
    p = (phone or "").strip()
    return p[-4:] if len(p) >= 4 else "????"


async def digest_speed_to_contact(result: dict[str, Any]) -> None:
    queued = int(result.get("queued") or 0)
    started = int(result.get("started") or 0)
    no_phone = int(result.get("no_phone") or 0)
    if not (queued or started):
        return
    mode = result.get("mode") or "review"
    text = (
        f"🚀 *Speed-to-Contact* (`{mode}`)\n"
        f"• Queued for approval: *{queued}*\n"
        f"• Auto-started: *{started}*\n"
        f"• No phone resolved: {no_phone}\n"
        f"_Review the outreach queue in Super CRM before send._"
    )
    await post_slack(text)


async def digest_paperwork_chase(result: dict[str, Any]) -> None:
    n1 = int(result.get("nudge_1_sent") or 0)
    n2 = int(result.get("nudge_2_sent") or 0)
    staff = int(result.get("staff_alerts") or 0)
    review = int(result.get("review_queued") or 0)
    if not (n1 or n2 or staff or review):
        return
    mode = result.get("mode") or "?"
    text = (
        f"📋 *Paperwork Chase* (`{mode}`)\n"
        f"• Review queue: *{review}*\n"
        f"• Client nudge 1: {n1} · nudge 2: {n2}\n"
        f"• Staff alerts (24h+): *{staff}*\n"
        f"_Unsigned packets need signatures — check Automations tab._"
    )
    await post_slack(text)


async def digest_intake_recovery(result: dict[str, Any]) -> None:
    sent = int(result.get("recovered_sent") or 0)
    review = int(result.get("review_queued") or 0)
    if not (sent or review):
        return
    mode = result.get("mode") or "?"
    text = (
        f"🔄 *Intake Recovery* (`{mode}`)\n"
        f"• Review queue: *{review}*\n"
        f"• Recovery messages sent: *{sent}*\n"
        f"_Abandoned intakes — finish or call back._"
    )
    await post_slack(text)


async def digest_poa_low_stock(rows: list[dict[str, Any]], threshold: int) -> None:
    if not rows:
        return
    lines = [f"📕 *POA Low Stock* (threshold ≤ {threshold})"]
    for r in rows[:12]:
        lines.append(
            f"• {r.get('surety_id', '?').upper()} tier {r.get('tier', '?')}: "
            f"*{r.get('available', 0)}* available"
        )
    await post_slack("\n".join(lines), webhook_env="SLACK_WEBHOOK_ERRORS")


async def digest_ops_sweep(action: str, counts: dict[str, Any], extras: str = "") -> None:
    """Generic Node-RED sweep digest (lead qual / lifecycle / risk)."""
    if not counts:
        return
    bits = " · ".join(f"{k}={v}" for k, v in counts.items() if v)
    if not bits:
        return
    text = f"☘️ *Automation Sweep — {action}*\n{bits}"
    if extras:
        text += f"\n{extras}"
    text += f"\n_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    await post_slack(text)
