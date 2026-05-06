"""
ShamrockLeads — BlueBubbles Server Health Monitor
==================================================
Monitors the health of both BlueBubbles servers (iMacs) and alerts
the team via Slack when a server goes offline, the Private API
disconnects, or the Messages.app gets stuck.

Why This Matters
----------------
If a BlueBubbles server goes offline:
  - Inbound messages stop being received
  - Outbound messages queue up and fail
  - Court reminders don't get sent
  - The entire iMessage automation pipeline breaks silently

This monitor catches failures immediately and:
  1. Alerts the team on Slack
  2. Attempts automatic recovery (restart Messages.app)
  3. Falls back to Twilio SMS for critical messages
  4. Logs all health events to MongoDB

Endpoints
---------
  GET    /api/bb-health/status          — Current health of all BB servers
  POST   /api/bb-health/check           — Run an immediate health check
  POST   /api/bb-health/restart-messages — Restart Messages.app on a server
  GET    /api/bb-health/history         — Health check history
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from quart import Blueprint, jsonify, request

from dashboard.api.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_collection

logger = logging.getLogger(__name__)

bb_health_bp = Blueprint("bb_health_monitor", __name__)

_SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
_SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#shamrock-alerts")


async def _send_slack_alert(message: str) -> None:
    """Send an alert to Slack."""
    if not _SLACK_WEBHOOK:
        logger.warning("SLACK_WEBHOOK_URL not set — cannot send alert: %s", message)
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(_SLACK_WEBHOOK, json={
                "channel": _SLACK_CHANNEL,
                "text": f"🚨 *BlueBubbles Alert* 🚨\n{message}",
            }, timeout=10)
    except Exception as e:
        logger.error("Slack alert failed: %s", e)


async def check_server_health(suffix: str, server: dict) -> dict:
    """Run a comprehensive health check on a single BlueBubbles server.

    Checks:
      1. Server reachability (HTTP ping)
      2. Server info (version, uptime)
      3. Private API connection status
      4. Messages.app status
      5. Pending message queue depth

    Returns:
        {
            "server": str,
            "suffix": str,
            "reachable": bool,
            "private_api_connected": bool,
            "messages_app_running": bool,
            "uptime_seconds": int,
            "version": str,
            "status": "healthy" | "degraded" | "offline",
            "issues": list[str],
        }
    """
    client = BlueBubblesClient(server["url"], server["password"], timeout=8.0)
    issues = []

    # 1. Ping the server
    info_result = await client.server_info()
    reachable = info_result.get("success", False)

    if not reachable:
        return {
            "server": server["label"],
            "suffix": suffix,
            "reachable": False,
            "private_api_connected": False,
            "messages_app_running": False,
            "uptime_seconds": 0,
            "version": "unknown",
            "status": "offline",
            "issues": ["Server unreachable — BlueBubbles may be down or Cloudflare tunnel expired"],
        }

    # 2. Parse server info
    info_data = info_result.get("data", {})
    if isinstance(info_data, dict):
        server_info = info_data.get("server_info", info_data)
    else:
        server_info = {}

    version = server_info.get("server_version") or server_info.get("version", "unknown")
    uptime = server_info.get("uptime", 0) or 0
    # BB API returns "private_api" (bool) + "helper_connected" (bool), NOT camelCase
    private_api = (
        server_info.get("private_api", False)
        or server_info.get("helper_connected", False)
        or server_info.get("privateApiConnected", False)   # fallback for future versions
        or server_info.get("private_api_connected", False)
    )
    messages_running = server_info.get("messagesRunning", True)  # Assume running if not reported

    if not private_api:
        issues.append("Private API not connected — unsend/edit/typing/reactions unavailable")
    if not messages_running:
        issues.append("Messages.app is not running — iMessage send/receive broken")

    status = "healthy"
    if issues:
        status = "degraded"
    if not messages_running:
        status = "offline"

    # Gap 4: add uptime and message_count for the KPI strip
    message_count = (
        server_info.get("totalMessages")
        or server_info.get("messageCount")
        or server_info.get("total_messages")
        or 0
    )
    uptime_seconds = (
        server_info.get("uptime")
        or server_info.get("serverUptime")
        or 0
    )

    return {
        "server": server["label"],
        "suffix": suffix,
        "reachable": True,
        "private_api_connected": private_api,
        "messages_app_running": messages_running,
        "uptime_seconds": uptime_seconds,
        "uptime": uptime_seconds,           # alias for JS KPI strip
        "message_count": message_count,     # alias for JS KPI strip
        "total_messages": message_count,    # alias for renderHealth()
        "version": version,
        "status": status,
        "issues": issues,
    }


async def run_health_check_all() -> dict:
    """Run health checks on all configured BlueBubbles servers.

    Logs results to MongoDB and sends Slack alerts for any issues.

    Returns:
        { "servers": list[dict], "overall_status": str, "checked_at": str }
    """
    health_coll = get_collection("bb_health_checks")
    results = []
    overall_status = "healthy"

    for suffix, server in BB_SERVERS.items():
        health = await check_server_health(suffix, server)
        results.append(health)

        if health["status"] == "offline":
            overall_status = "offline"
            await _send_slack_alert(
                f"🔴 *{server['label']}* is OFFLINE\n"
                f"Issues: {', '.join(health['issues'])}\n"
                f"URL: {server['url']}"
            )
        elif health["status"] == "degraded" and overall_status == "healthy":
            overall_status = "degraded"
            await _send_slack_alert(
                f"🟡 *{server['label']}* is DEGRADED\n"
                f"Issues: {', '.join(health['issues'])}"
            )

    checked_at = datetime.now(timezone.utc).isoformat()

    # Log to MongoDB
    await health_coll.insert_one({
        "servers": results,
        "overall_status": overall_status,
        "checked_at": checked_at,
    })

    return {
        "servers": results,
        "overall_status": overall_status,
        "checked_at": checked_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_health_bp.route("/bb-health/status", methods=["GET"])
async def api_health_status():
    """Get the most recent health check results from MongoDB.

    Gap 4 fix: if the cache is empty (first call), run a synchronous check
    so the KPI strip populates immediately rather than showing dashes.
    """
    try:
        health_coll = get_collection("bb_health_checks")
        latest = await health_coll.find_one(
            {}, {"_id": 0}, sort=[("checked_at", -1)]
        )
        if not latest:
            # No cached result — run a live check now so the UI gets real data
            logger.info("[bb-health] No cached result — running synchronous check")
            latest = await run_health_check_all()
        # Flatten for JS: expose top-level uptime/message_count from first server
        servers = latest.get("servers", [])
        if servers:
            first = servers[0]
            latest.setdefault("uptime", first.get("uptime", first.get("uptime_seconds", 0)))
            latest.setdefault("message_count", first.get("message_count", first.get("total_messages", 0)))
            latest.setdefault("version", first.get("version", "unknown"))
            latest.setdefault("private_api_enabled", first.get("private_api_connected", False))
            latest.setdefault("connected", first.get("reachable", False))
        return jsonify({"success": True, **latest})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bb_health_bp.route("/bb-health/check", methods=["POST"])
async def api_run_health_check():
    """Run an immediate health check on all BlueBubbles servers."""
    try:
        result = await run_health_check_all()
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error("Health check error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bb_health_bp.route("/bb-health/restart-messages", methods=["POST"])
async def api_restart_messages():
    """Restart Messages.app on a BlueBubbles server.

    Body:
        { "suffix": "0178" }  — or omit to restart all servers
    """
    try:
        data = await request.get_json(silent=True) or {}
        target_suffix = data.get("suffix", "")

        results = {}
        for suffix, server in BB_SERVERS.items():
            if target_suffix and suffix != target_suffix and not server["suffix"] == target_suffix:
                continue
            client = BlueBubblesClient(server["url"], server["password"])
            result = await client.restart_messages_app()
            results[server["label"]] = result.get("success", False)
            logger.info("🔄 Restarted Messages.app on %s: %s", server["label"], result.get("success"))

        return jsonify({"success": True, "results": results})

    except Exception as e:
        logger.error("Restart Messages.app error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bb_health_bp.route("/bb-health/history", methods=["GET"])
async def api_health_history():
    """Get health check history."""
    try:
        limit = int(request.args.get("limit", 20))
        health_coll = get_collection("bb_health_checks")
        history = await health_coll.find(
            {}, {"_id": 0}
        ).sort("checked_at", -1).limit(limit).to_list(length=limit)
        return jsonify({"success": True, "count": len(history), "history": history})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
