"""
ShamrockLeads — Tailscale Network Configuration

Tailscale provides a zero-config WireGuard mesh (tailnet) connecting:
  • Hetzner VPS (shamrock-vps)     — scraper engine, dashboard, Docker stack
  • Office iMac (shamrocksimac)    — BlueBubbles, residential exit node
  • Laptop (shamrock-laptop)       — dev/ops access

Benefits over ngrok/frp/SSH tunnels:
  1. Direct peer-to-peer WireGuard — lower latency, no relay hops
  2. MagicDNS — services reachable by hostname (shamrocksimac:1234)
  3. Exit node — route scraper traffic through office residential IP
  4. ACLs — fine-grained access control per device/user/port
  5. Funnel — optional public HTTPS ingress without nginx/ngrok

Environment variables:
  TAILSCALE_ENABLED=true           — master switch for tailnet routing
  TAILSCALE_AUTHKEY=tskey-auth-... — pre-auth key for Docker sidecar
  TAILSCALE_TAILNET=shamrockbailbonds.biz
  TAILSCALE_IMAC_HOSTNAME=shamrocksimac
  TAILSCALE_VPS_HOSTNAME=shamrock-vps
  TAILSCALE_LAPTOP_HOSTNAME=shamrock-laptop
  TAILSCALE_IMAC_IP=100.102.10.86
  TAILSCALE_EXIT_NODE=shamrocksimac  — residential exit node for scrapers
"""
from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TailscaleConfig:
    """Tailscale tailnet configuration and service discovery."""

    enabled: bool = field(default_factory=lambda: os.getenv("TAILSCALE_ENABLED", "true").lower() == "true")
    authkey: str = field(default_factory=lambda: os.getenv("TAILSCALE_AUTHKEY", ""))
    tailnet: str = field(default_factory=lambda: os.getenv("TAILSCALE_TAILNET", "shamrockbailbonds.biz"))

    # ── Device Hostnames (MagicDNS) ──
    imac_hostname: str = field(default_factory=lambda: os.getenv("TAILSCALE_IMAC_HOSTNAME", "shamrocksimac"))
    vps_hostname: str = field(default_factory=lambda: os.getenv("TAILSCALE_VPS_HOSTNAME", "shamrock-vps"))
    laptop_hostname: str = field(default_factory=lambda: os.getenv("TAILSCALE_LAPTOP_HOSTNAME", "shamrock-laptop"))

    # ── Known Tailscale IPs (fallback if MagicDNS fails) ──
    imac_ip: str = field(default_factory=lambda: os.getenv("TAILSCALE_IMAC_IP", "100.102.10.86"))
    vps_ip: str = field(default_factory=lambda: os.getenv("TAILSCALE_VPS_IP", ""))
    laptop_ip: str = field(default_factory=lambda: os.getenv("TAILSCALE_LAPTOP_IP", ""))

    # ── Exit Node (residential proxy routing) ──
    exit_node: str = field(default_factory=lambda: os.getenv("TAILSCALE_EXIT_NODE", "shamrocksimac"))

    # ── Service Ports ──
    bb_port: int = 1234          # BlueBubbles on iMac
    socks_port: int = 1080       # SOCKS5 proxy (Tailscale replaces SSH tunnel)
    osint_port: int = 5065       # OSINT worker
    dashboard_port: int = 5050   # Dashboard internal
    node_red_port: int = 1880    # Node-RED

    def __post_init__(self):
        """Resolve VPS IP from Tailscale if not explicitly set."""
        if not self.vps_ip:
            self.vps_ip = self._resolve_ts_ip(self.vps_hostname)

    # ── Service URLs ──────────────────────────────────────────────────────────

    @property
    def bb_url_tailscale(self) -> str:
        """BlueBubbles URL via Tailscale (direct peer-to-peer)."""
        host = self._resolve_imac()
        return f"http://{host}:{self.bb_port}"

    @property
    def imac_ssh(self) -> str:
        """SSH command for iMac via Tailscale."""
        host = self._resolve_imac()
        return f"ssh shamrockbailbonds@{host}"

    @property
    def imac_socks_url(self) -> str:
        """SOCKS5 proxy URL via Tailscale (replaces SSH -R tunnel)."""
        host = self._resolve_imac()
        return f"socks5://{host}:{self.socks_port}"

    # ── Health & Discovery ────────────────────────────────────────────────────

    def is_imac_reachable(self, timeout: float = 3.0) -> bool:
        """Quick TCP probe to check if iMac is reachable via Tailscale."""
        host = self._resolve_imac()
        return self._tcp_probe(host, self.bb_port, timeout)

    def is_tailnet_up(self, timeout: float = 2.0) -> bool:
        """Check if the Tailscale interface is up by resolving a known peer."""
        if not self.enabled:
            return False
        try:
            ip = socket.getaddrinfo(
                self.imac_hostname, None, socket.AF_INET, socket.SOCK_STREAM
            )
            return bool(ip and ip[0][4][0].startswith("100."))
        except (socket.gaierror, OSError):
            return False

    def get_bb_url_with_fallback(self, ngrok_url: str = "") -> str:
        """
        Return the best BlueBubbles URL with automatic failover:
          1. Tailscale direct (lowest latency, no relay)
          2. ngrok static domain (public fallback)
          3. frp TCP proxy (legacy fallback)
        """
        if self.enabled and self.is_imac_reachable(timeout=2.0):
            logger.debug("BB URL: using Tailscale direct → %s", self.bb_url_tailscale)
            return self.bb_url_tailscale

        if ngrok_url:
            logger.info("BB URL: Tailscale unreachable, falling back to ngrok → %s", ngrok_url)
            return ngrok_url

        # Final fallback: frp TCP (if configured)
        frp_url = os.getenv("BLUEBUBBLES_FRP_URL", "")
        if frp_url:
            logger.info("BB URL: falling back to frp → %s", frp_url)
            return frp_url

        logger.warning("BB URL: all paths failed, returning ngrok default")
        return ngrok_url or ""

    def get_proxy_url_with_fallback(self, warren_url: str = "") -> Optional[str]:
        """
        Return the best SOCKS5 proxy URL for residential egress:
          1. Tailscale exit node (iMac residential IP, zero-config)
          2. Warren hub (self-hosted proxy pool)
          3. None (direct connection)
        """
        if self.enabled and self._tcp_probe(self._resolve_imac(), self.socks_port, 2.0):
            return self.imac_socks_url
        if warren_url:
            return warren_url
        return None

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _resolve_imac(self) -> str:
        """Resolve iMac address: prefer MagicDNS hostname, fallback to IP."""
        resolved = self._resolve_ts_ip(self.imac_hostname)
        return resolved if resolved else self.imac_ip

    @staticmethod
    def _resolve_ts_ip(hostname: str) -> str:
        """Resolve a Tailscale MagicDNS hostname to its 100.x.x.x IP."""
        try:
            results = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
            if results:
                ip = results[0][4][0]
                if ip.startswith("100."):
                    return ip
                return ip
        except (socket.gaierror, OSError):
            pass
        return ""

    @staticmethod
    def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
        """Quick TCP connect probe."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except (OSError, socket.error):
            return False

    def log_status(self):
        """Log current Tailscale configuration status at startup."""
        if not self.enabled:
            logger.info("🔌 Tailscale: DISABLED (using legacy ngrok/frp paths)")
            return

        tailnet_up = self.is_tailnet_up()
        imac_up = self.is_imac_reachable() if tailnet_up else False

        logger.info("🌐 Tailscale: ENABLED")
        logger.info("   Tailnet: %s", self.tailnet)
        logger.info("   Network: %s", "✅ UP" if tailnet_up else "❌ DOWN")
        logger.info("   iMac (%s): %s", self.imac_hostname, "✅ reachable" if imac_up else "❌ unreachable")
        logger.info("   Exit Node: %s", self.exit_node)
        if imac_up:
            logger.info("   BB Direct URL: %s", self.bb_url_tailscale)
        else:
            logger.info("   BB Fallback: ngrok/frp (Tailscale peer offline)")


# ── Singleton ─────────────────────────────────────────────────────────────────
ts_config = TailscaleConfig()
