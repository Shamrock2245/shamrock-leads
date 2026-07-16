"""
Autonomous Proxy Engine (APE) v2.0
Integrates Warren, S5W2C, and Stormsia for self-hosted proxy ecosystem.

Features:
- Automatic failover (Warren → S5W2C → Stormsia)
- Config-gated sources (skip unconfigured / unreachable)
- Health checking and proxy validation
- Session-based sticky routing
- Regional routing support
- Autonomous cache management
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ProxyType(Enum):
    """Proxy source types."""

    WARREN_RESIDENTIAL = "warren_residential"
    S5W2C_MOBILE = "s5w2c_mobile"
    STORMSIA_FREE = "stormsia_free"


class RoutingMode(Enum):
    """Warren routing modes."""

    RANDOM = "random"
    STICKY_SESSION = "sticky_session"
    REGION = "region"
    DEVICE = "device"
    GROUP = "group"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_proxy_url(proxy: str, protocol: str = "socks5") -> str:
    """Ensure proxy strings are full URLs for HTTP clients."""
    p = (proxy or "").strip()
    if not p:
        return p
    if "://" in p:
        return p
    # Bare host:port from some lists
    if protocol in {"socks5", "socks4"}:
        return f"{protocol}://{p}"
    return f"http://{p}"


def _parse_host_port(hub_url: str, default_port: int = 8000) -> tuple[str, int]:
    """Extract host:port from a Warren hub URL (with or without scheme)."""
    host = (hub_url or "").strip()
    for prefix in ("http://", "https://", "socks5://", "socks5h://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    # Drop credentials if present
    if "@" in host:
        host = host.split("@", 1)[1]
    # Drop path
    host = host.split("/", 1)[0]
    if ":" in host:
        h, p = host.rsplit(":", 1)
        try:
            return h, int(p)
        except ValueError:
            return h, default_port
    return host, default_port


@dataclass
class ProxyMetrics:
    """Metrics for a proxy."""

    proxy: str
    proxy_type: ProxyType
    success_count: int = 0
    failure_count: int = 0
    last_used: Optional[datetime] = None
    last_failed: Optional[datetime] = None
    avg_response_time_ms: float = 0.0
    _response_samples: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def is_healthy(self) -> bool:
        if self.failure_count > 5 and self.success_rate < 0.2:
            return False
        if self.last_failed:
            if (datetime.now() - self.last_failed).total_seconds() < 300:
                if self.success_count == 0 or self.success_rate < 0.5:
                    return False
        return True

    def note_success(self, response_time_ms: float = 0.0) -> None:
        self.success_count += 1
        self.last_used = datetime.now()
        if response_time_ms > 0:
            self._response_samples += 1
            n = self._response_samples
            self.avg_response_time_ms = (
                (self.avg_response_time_ms * (n - 1) + response_time_ms) / n
            )


class WarrenProxyManager:
    """Manages Warren residential proxy pool."""

    def __init__(self, hub_url: str, password: str, username: str = "warren"):
        """
        Args:
            hub_url: Warren proxy listen host:port (e.g. "178.156.179.237:8000")
            password: Proxy password printed/set by the hub
            username: Proxy username (default "warren")
        """
        self.hub_url = hub_url
        self.password = password
        self.username = username or "warren"
        self.session_keys: Dict[str, str] = {}
        self._last_health_check: Optional[datetime] = None
        self._last_health_ok: bool = False

    @property
    def is_configured(self) -> bool:
        """True when hub URL + non-placeholder password are present."""
        if not self.hub_url or not self.password:
            return False
        placeholders = {
            "password",
            "your-secure-password",
            "YOUR_WARREN_TOKEN_HERE",
            "changeme",
            "test",
        }
        return self.password not in placeholders

    def is_available(self, timeout: float = 2.0, cache_seconds: float = 30.0) -> bool:
        """TCP connect check against the proxy port (cached briefly)."""
        if not self.is_configured:
            return False
        now = datetime.now()
        if (
            self._last_health_check
            and (now - self._last_health_check).total_seconds() < cache_seconds
        ):
            return self._last_health_ok
        host, port = _parse_host_port(self.hub_url, default_port=8000)
        ok = False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok = True
        except OSError as e:
            logger.debug("Warren hub not reachable at %s:%s: %s", host, port, e)
            ok = False
        self._last_health_check = now
        self._last_health_ok = ok
        return ok

    def get_proxy(
        self,
        routing_mode: RoutingMode = RoutingMode.RANDOM,
        routing_param: Optional[str] = None,
    ) -> Optional[str]:
        """Build a Warren proxy URL for the requested routing mode."""
        if not self.is_configured:
            return None

        user = self.username
        if routing_mode == RoutingMode.STICKY_SESSION:
            session_id = routing_param or "default"
            user = f"{self.username}-session-{session_id}"
        elif routing_mode == RoutingMode.REGION:
            region = routing_param or "US"
            user = f"{self.username}-region-{region}"
        elif routing_mode == RoutingMode.DEVICE:
            device = routing_param or "primary"
            user = f"{self.username}+{device}"
        elif routing_mode == RoutingMode.GROUP:
            group = routing_param or "residential"
            user = f"{self.username}-group-{group}"

        return f"http://{user}:{self.password}@{self.hub_url}"


class S5W2CProxyManager:
    """Manages S5W2C Android mobile proxy (WiFi → cellular exit)."""

    def __init__(self, phone_ip: str, port: int = 1080):
        self.phone_ip = phone_ip
        self.port = port

    @property
    def is_configured(self) -> bool:
        if not self.phone_ip:
            return False
        placeholders = {"192.168.1.100", "YOUR_ANDROID_PHONE_IP", "0.0.0.0"}
        return self.phone_ip not in placeholders

    def get_proxy(self) -> Optional[str]:
        if not self.is_configured:
            return None
        return f"socks5://{self.phone_ip}:{self.port}"

    def is_available(self) -> bool:
        if not self.is_configured:
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.phone_ip, self.port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug("S5W2C availability check failed: %s", e)
            return False


class StormsiaBridgeManager:
    """Manages Stormsia free proxy list (public GitHub aggregator)."""

    STORMSIA_BASE = "https://raw.githubusercontent.com/stormsia/proxy-list/main"
    PROTOCOLS = ["socks5", "socks4", "http"]
    CACHE_TTL = 1800  # 30 minutes

    def __init__(self, cache_ttl: int = CACHE_TTL):
        self.cache_ttl = cache_ttl
        self.proxy_cache: Dict[str, List[str]] = {}
        self.cache_timestamp: Dict[str, datetime] = {}
        self._rr_index: Dict[str, int] = {}

    def fetch_proxies(self, protocol: str = "socks5", timeout: int = 10) -> List[str]:
        if protocol in self.proxy_cache and self._is_cache_valid(protocol):
            logger.debug("Using cached %s proxies", protocol)
            return self.proxy_cache[protocol]

        url = f"{self.STORMSIA_BASE}/{protocol}.txt"
        try:
            logger.info("Fetching %s proxies from Stormsia...", protocol)
            with urllib.request.urlopen(url, timeout=timeout) as response:
                content = response.read().decode("utf-8").strip()
                raw = [p.strip() for p in content.split("\n") if p.strip()]
                proxies = [_normalize_proxy_url(p, protocol) for p in raw]

                self.proxy_cache[protocol] = proxies
                self.cache_timestamp[protocol] = datetime.now()
                logger.info("✅ Fetched %d %s proxies from Stormsia", len(proxies), protocol)
                return proxies
        except Exception as e:
            logger.error("Failed to fetch %s proxies: %s", protocol, e)
            if protocol in self.proxy_cache:
                logger.warning("Using stale cache for %s", protocol)
                return self.proxy_cache[protocol]
            return []

    def next_proxy(
        self, protocol: str = "socks5", exclude: Optional[Set[str]] = None
    ) -> Optional[str]:
        """Round-robin next non-excluded Stormsia proxy."""
        proxies = self.fetch_proxies(protocol=protocol)
        if not proxies:
            return None
        exclude = exclude or set()
        start = self._rr_index.get(protocol, 0) % len(proxies)
        for offset in range(len(proxies)):
            idx = (start + offset) % len(proxies)
            candidate = proxies[idx]
            if candidate not in exclude:
                self._rr_index[protocol] = idx + 1
                return candidate
        return None

    def _is_cache_valid(self, protocol: str) -> bool:
        if protocol not in self.cache_timestamp:
            return False
        age = (datetime.now() - self.cache_timestamp[protocol]).total_seconds()
        return age < self.cache_ttl


class AutonomousProxyEngine:
    """
    Autonomous Proxy Engine (APE) — manages all proxy sources.

    Fallback chain:
    1. Warren (residential, when configured + reachable)
    2. S5W2C (mobile data, when phone is on LAN)
    3. Stormsia (free public list)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}

        self.warren_manager = WarrenProxyManager(
            hub_url=config.get(
                "warren_hub_url",
                os.getenv("WARREN_HUB_URL", "178.156.179.237:8000"),
            ),
            password=config.get(
                "warren_password",
                os.getenv("WARREN_PASSWORD", ""),
            ),
            username=config.get(
                "warren_username",
                os.getenv("WARREN_PROXY_USER", "warren"),
            ),
        )

        self.s5w2c_manager = S5W2CProxyManager(
            phone_ip=config.get(
                "s5w2c_phone_ip",
                os.getenv("S5W2C_PHONE_IP", ""),
            ),
            port=int(
                config.get("s5w2c_port", os.getenv("S5W2C_PORT", "1080"))
            ),
        )

        self.stormsia_manager = StormsiaBridgeManager(
            cache_ttl=int(
                config.get(
                    "stormsia_cache_ttl",
                    os.getenv("STORMSIA_CACHE_TTL", "1800"),
                )
            )
        )

        # WARREN_ENABLED defaults to True when password is set
        self.warren_enabled = config.get(
            "warren_enabled",
            _env_bool("WARREN_ENABLED", default=self.warren_manager.is_configured),
        )
        self.s5w2c_enabled = config.get(
            "s5w2c_enabled",
            _env_bool("S5W2C_ENABLED", default=self.s5w2c_manager.is_configured),
        )
        self.stormsia_enabled = config.get(
            "stormsia_enabled",
            _env_bool("STORMSIA_ENABLED", default=True),
        )

        self.metrics: Dict[str, ProxyMetrics] = {}
        self.failed_proxies: Set[str] = set()
        self.last_rotation = datetime.now()
        self._recovery_timers: Dict[str, threading.Timer] = {}

    def get_next_proxy(
        self, prefer_residential: bool = True, protocol: str = "socks5"
    ) -> Optional[str]:
        """
        Get next proxy with automatic failover.

        When prefer_residential=True: Warren → S5W2C → Stormsia
        When prefer_residential=False: Stormsia first, then others
        """
        chain: List[str]
        if prefer_residential:
            chain = ["warren", "s5w2c", "stormsia"]
        else:
            chain = ["stormsia", "warren", "s5w2c"]

        for source in chain:
            proxy = self._proxy_from_source(source, protocol=protocol)
            if proxy and proxy not in self.failed_proxies:
                logger.debug("Using %s proxy", source)
                return proxy

        logger.error("All proxy sources exhausted")
        return None

    def _proxy_from_source(self, source: str, protocol: str = "socks5") -> Optional[str]:
        if source == "warren":
            if not self.warren_enabled:
                return None
            if not self.warren_manager.is_available():
                return None
            return self.warren_manager.get_proxy()

        if source == "s5w2c":
            if not self.s5w2c_enabled:
                return None
            if not self.s5w2c_manager.is_available():
                return None
            return self.s5w2c_manager.get_proxy()

        if source == "stormsia":
            if not self.stormsia_enabled:
                return None
            return self.stormsia_manager.next_proxy(
                protocol=protocol, exclude=self.failed_proxies
            )

        return None

    def get_sticky_proxy(self, session_id: str) -> Optional[str]:
        """Sticky session via Warren (falls back to next available if Warren down)."""
        if self.warren_enabled and self.warren_manager.is_available():
            proxy = self.warren_manager.get_proxy(
                routing_mode=RoutingMode.STICKY_SESSION,
                routing_param=session_id,
            )
            if proxy and proxy not in self.failed_proxies:
                return proxy
        return self.get_next_proxy(prefer_residential=True)

    def get_regional_proxy(self, region: str) -> Optional[str]:
        """Regional routing via Warren; falls back to pool if unavailable."""
        if self.warren_enabled and self.warren_manager.is_available():
            proxy = self.warren_manager.get_proxy(
                routing_mode=RoutingMode.REGION,
                routing_param=region,
            )
            if proxy and proxy not in self.failed_proxies:
                return proxy
        return self.get_next_proxy(prefer_residential=True)

    def mark_proxy_failed(self, proxy: str, duration_seconds: int = 300) -> None:
        """Skip proxy for N seconds (auto-recovery)."""
        if not proxy:
            return
        self.failed_proxies.add(proxy)

        # Cancel existing recovery timer for this proxy
        old = self._recovery_timers.pop(proxy, None)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass

        def recover() -> None:
            self.failed_proxies.discard(proxy)
            self._recovery_timers.pop(proxy, None)
            logger.info("Proxy recovered: %s...", proxy[:50])

        timer = threading.Timer(duration_seconds, recover)
        timer.daemon = True
        timer.start()
        self._recovery_timers[proxy] = timer

    def record_success(self, proxy: str, response_time_ms: float = 0.0) -> None:
        if not proxy:
            return
        if proxy not in self.metrics:
            self.metrics[proxy] = ProxyMetrics(
                proxy=proxy, proxy_type=self._infer_type(proxy)
            )
        self.metrics[proxy].note_success(response_time_ms)
        # Successful use clears failed mark
        self.failed_proxies.discard(proxy)

    def record_failure(self, proxy: str) -> None:
        if not proxy:
            return
        if proxy not in self.metrics:
            self.metrics[proxy] = ProxyMetrics(
                proxy=proxy, proxy_type=self._infer_type(proxy)
            )
        self.metrics[proxy].failure_count += 1
        self.metrics[proxy].last_failed = datetime.now()
        self.mark_proxy_failed(proxy)

    def _infer_type(self, proxy: str) -> ProxyType:
        if "warren" in proxy or (
            self.warren_manager.hub_url and self.warren_manager.hub_url in proxy
        ):
            return ProxyType.WARREN_RESIDENTIAL
        if proxy.startswith("socks5://") and self.s5w2c_manager.phone_ip in proxy:
            return ProxyType.S5W2C_MOBILE
        return ProxyType.STORMSIA_FREE

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_proxies_tracked": len(self.metrics),
            "failed_proxies": len(self.failed_proxies),
            "sources": {
                "warren_enabled": self.warren_enabled,
                "warren_configured": self.warren_manager.is_configured,
                "warren_reachable": (
                    self.warren_manager.is_available() if self.warren_enabled else False
                ),
                "s5w2c_enabled": self.s5w2c_enabled,
                "s5w2c_available": (
                    self.s5w2c_manager.is_available() if self.s5w2c_enabled else False
                ),
                "stormsia_enabled": self.stormsia_enabled,
                "stormsia_cached": {
                    proto: len(proxies)
                    for proto, proxies in self.stormsia_manager.proxy_cache.items()
                },
            },
            "proxies": {
                proxy: {
                    "success_count": m.success_count,
                    "failure_count": m.failure_count,
                    "success_rate": m.success_rate,
                    "is_healthy": m.is_healthy,
                    "avg_response_time_ms": m.avg_response_time_ms,
                    "proxy_type": m.proxy_type.value,
                }
                for proxy, m in self.metrics.items()
            },
        }


# Global APE instance
_ape_instance: Optional[AutonomousProxyEngine] = None
_ape_lock = threading.Lock()


def get_ape() -> AutonomousProxyEngine:
    """Get or create global APE instance (thread-safe)."""
    global _ape_instance
    if _ape_instance is None:
        with _ape_lock:
            if _ape_instance is None:
                _ape_instance = AutonomousProxyEngine()
    return _ape_instance


def init_ape(config: Dict[str, Any]) -> AutonomousProxyEngine:
    """Initialize (or replace) APE with custom config."""
    global _ape_instance
    with _ape_lock:
        _ape_instance = AutonomousProxyEngine(config)
        return _ape_instance


def reset_ape() -> None:
    """Clear global APE instance (tests)."""
    global _ape_instance
    with _ape_lock:
        _ape_instance = None
