"""
ShamrockLeads — Tailscale Status API Router

Provides dashboard endpoints for monitoring Tailscale mesh health,
peer connectivity, and service failover status.

Endpoints:
  GET  /api/tailscale/status    — Full tailnet health summary
  POST /api/tailscale/check     — Trigger immediate health check
  GET  /api/tailscale/peers     — List all known peers with latency
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

tailscale_bp = APIRouter(tags=["tailscale"])


@tailscale_bp.get("/api/tailscale/status")
async def api_tailscale_status():
    """Return current Tailscale mesh health status."""
    try:
        from dashboard.services.tailscale_health import get_ts_health_monitor
        monitor = get_ts_health_monitor()
        return monitor.status_dict()
    except Exception as e:
        logger.error("Tailscale status error: %s", e)
        return JSONResponse(
            {"enabled": False, "error": str(e)},
            status_code=500,
        )


@tailscale_bp.post("/api/tailscale/check")
async def api_tailscale_check():
    """Trigger an immediate health check on all Tailscale peers."""
    try:
        from dashboard.services.tailscale_health import get_ts_health_monitor
        monitor = get_ts_health_monitor()
        peers = await monitor.check_all_peers()
        return {
            "success": True,
            "peers_checked": len(peers),
            "status": monitor.status_dict(),
        }
    except Exception as e:
        logger.error("Tailscale check error: %s", e)
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500,
        )


@tailscale_bp.get("/api/tailscale/peers")
async def api_tailscale_peers():
    """List all known Tailscale peers with connectivity info."""
    try:
        from dashboard.services.tailscale_health import get_ts_health_monitor
        monitor = get_ts_health_monitor()
        status = monitor.status_dict()
        return {
            "tailnet": status.get("tailnet", ""),
            "tailnet_up": status.get("tailnet_up", False),
            "peers": status.get("peers", {}),
        }
    except Exception as e:
        logger.error("Tailscale peers error: %s", e)
        return JSONResponse(
            {"peers": {}, "error": str(e)},
            status_code=500,
        )
