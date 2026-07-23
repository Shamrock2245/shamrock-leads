"""
ShamrockLeads — Tailscale Health Monitor Service

Provides health checking and automatic failover for Tailscale-connected
services. Integrates with the BlueBubbles health monitor to prefer
Tailscale direct connections over ngrok/frp when available.

Architecture:
  1. Periodic probe of Tailscale peers (iMac, laptop)
  2. Automatic BB URL failover: Tailscale → ngrok → frp
  3. Proxy path failover: Tailscale SOCKS → Warren → direct
  4. Slack alerts on tailnet degradation
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PeerStatus:
    """Health status of a Tailscale peer."""
    hostname: str
    ip: str
    reachable: bool = False
    latency_ms: float = -1.0
    last_check: float = 0.0
    consecutive_failures: int = 0
    is_exit_node: bool = False
    services: Dict[str, bool] = field(default_factory=dict)


class TailscaleHealthMonitor:
    """
    Monitors Tailscale peer health and provides failover logic.

    Runs periodic health checks against known peers and maintains
    a status cache that other services (BB client, proxy engine)
    can query for routing decisions.
    """

    def __init__(self):
        from config.tailscale import ts_config
        self.config = ts_config
        self._peers: Dict[str, PeerStatus] = {}
        self._last_full_check: float = 0.0
        self._check_interval: float = 30.0  # seconds

        # Initialize known peers
        if self.config.enabled:
            self._peers["imac"] = PeerStatus(
                hostname=self.config.imac_hostname,
                ip=self.config.imac_ip,
                is_exit_node=True,
                services={"bluebubbles": False, "socks": False, "ssh": False},
            )

    @property
    def imac_healthy(self) -> bool:
        """Quick check: is the iMac reachable via Tailscale?"""
        peer = self._peers.get("imac")
        if not peer:
            return False
        # Use cached result if recent (< 30s)
        if time.time() - peer.last_check < self._check_interval:
            return peer.reachable
        # Otherwise do a live check
        return self.config.is_imac_reachable(timeout=2.0)

    @property
    def bb_via_tailscale(self) -> bool:
        """Is BlueBubbles reachable directly via Tailscale?"""
        peer = self._peers.get("imac")
        if not peer:
            return False
        return peer.services.get("bluebubbles", False)

    @property
    def socks_via_tailscale(self) -> bool:
        """Is the SOCKS5 proxy reachable via Tailscale?"""
        peer = self._peers.get("imac")
        if not peer:
            return False
        return peer.services.get("socks", False)

    async def check_all_peers(self) -> Dict[str, PeerStatus]:
        """Run health checks on all known Tailscale peers."""
        if not self.config.enabled:
            return {}

        for name, peer in self._peers.items():
            await self._check_peer(peer)

        self._last_full_check = time.time()
        return self._peers

    async def _check_peer(self, peer: PeerStatus) -> None:
        """Check a single peer's connectivity and services."""
        peer.last_check = time.time()
        host = peer.ip or peer.hostname

        # Basic reachability (TCP probe on SSH port 22)
        reachable = await asyncio.get_event_loop().run_in_executor(
            None, self.config._tcp_probe, host, 22, 3.0
        )
        peer.reachable = reachable

        if not reachable:
            peer.consecutive_failures += 1
            peer.services = {k: False for k in peer.services}
            if peer.consecutive_failures == 3:
                logger.warning(
                    "🔴 Tailscale peer '%s' (%s) unreachable for %d checks",
                    peer.hostname, peer.ip, peer.consecutive_failures
                )
            return

        peer.consecutive_failures = 0

        # Check individual services
        if "bluebubbles" in peer.services:
            peer.services["bluebubbles"] = await asyncio.get_event_loop().run_in_executor(
                None, self.config._tcp_probe, host, self.config.bb_port, 2.0
            )

        if "socks" in peer.services:
            peer.services["socks"] = await asyncio.get_event_loop().run_in_executor(
                None, self.config._tcp_probe, host, self.config.socks_port, 2.0
            )

        if "ssh" in peer.services:
            peer.services["ssh"] = await asyncio.get_event_loop().run_in_executor(
                None, self.config._tcp_probe, host, 22, 2.0
            )

        # Measure latency (TCP connect time to SSH port)
        peer.latency_ms = await self._measure_latency(host, 22)

    async def _measure_latency(self, host: str, port: int) -> float:
        """Measure TCP connect latency in milliseconds."""
        import socket

        def _probe():
            try:
                start = time.time()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect((host, port))
                elapsed = (time.time() - start) * 1000
                sock.close()
                return round(elapsed, 1)
            except (OSError, socket.error):
                return -1.0

        return await asyncio.get_event_loop().run_in_executor(None, _probe)

    def get_best_bb_url(self) -> str:
        """
        Return the best BlueBubbles URL with Tailscale-aware failover.

        Priority:
          1. Tailscale direct (http://shamrocksimac:1234) — lowest latency
          2. ngrok static domain — public fallback
          3. frp TCP proxy — legacy fallback
        """
        ngrok_url = os.getenv("BLUEBUBBLES_URL_0178", "")

        if self.config.enabled and self.bb_via_tailscale:
            return self.config.bb_url_tailscale

        # Live probe if cached status is stale
        if self.config.enabled and self.config.is_imac_reachable(timeout=2.0):
            return self.config.bb_url_tailscale

        return ngrok_url

    def get_best_proxy_url(self) -> Optional[str]:
        """
        Return the best SOCKS5 proxy URL for residential egress.

        Priority:
          1. Tailscale SOCKS (shamrocksimac:1080) — residential, zero-config
          2. Warren hub — self-hosted proxy pool
          3. None — direct connection
        """
        warren_url = os.getenv("WARREN_PROXY_URL", "")

        if self.config.enabled and self.socks_via_tailscale:
            return self.config.imac_socks_url

        if warren_url:
            return warren_url

        return None

    def status_dict(self) -> dict:
        """Return a JSON-serializable status summary for the dashboard."""
        return {
            "enabled": self.config.enabled,
            "tailnet": self.config.tailnet,
            "tailnet_up": self.config.is_tailnet_up(),
            "peers": {
                name: {
                    "hostname": peer.hostname,
                    "ip": peer.ip,
                    "reachable": peer.reachable,
                    "latency_ms": peer.latency_ms,
                    "services": peer.services,
                    "consecutive_failures": peer.consecutive_failures,
                    "is_exit_node": peer.is_exit_node,
                }
                for name, peer in self._peers.items()
            },
            "active_bb_url": self.get_best_bb_url(),
            "active_proxy_url": self.get_best_proxy_url(),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_monitor: Optional[TailscaleHealthMonitor] = None


def get_ts_health_monitor() -> TailscaleHealthMonitor:
    """Get or create the Tailscale health monitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = TailscaleHealthMonitor()
    return _monitor
