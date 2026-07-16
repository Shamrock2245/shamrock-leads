"""
Autonomous Proxy Engine (APE) v2.0
Integrates Warren, S5W2C, and Stormsia for self-hosted proxy ecosystem.

Features:
- Automatic failover (Warren → S5W2C → Stormsia)
- Health checking and proxy validation
- Session-based sticky routing
- Regional routing support
- Autonomous cache management
"""

import logging
import os
import time
import urllib.request
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Set
from dataclasses import dataclass, field
from enum import Enum
import json

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
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    @property
    def is_healthy(self) -> bool:
        """Determine if proxy is healthy."""
        if self.failure_count > 5:
            return False
        if self.last_failed:
            # Skip failed proxies for 5 minutes
            if (datetime.now() - self.last_failed).total_seconds() < 300:
                return False
        return True


class WarrenProxyManager:
    """Manages Warren residential proxy pool."""
    
    def __init__(self, hub_url: str, password: str):
        """
        Initialize Warren proxy manager.
        
        Args:
            hub_url: Warren hub URL (e.g., "5.161.126.32:8000")
            password: Warren proxy password
        """
        self.hub_url = hub_url
        self.password = password
        self.session_keys: Dict[str, str] = {}
    
    def get_proxy(self, routing_mode: RoutingMode = RoutingMode.RANDOM, 
                  routing_param: Optional[str] = None) -> str:
        """
        Get Warren proxy with routing mode.
        
        Args:
            routing_mode: Routing strategy
            routing_param: Parameter for routing (session ID, region, device, group)
        
        Returns:
            Proxy URL
        """
        if routing_mode == RoutingMode.STICKY_SESSION:
            session_id = routing_param or "default"
            return f"http://warren-session-{session_id}:{self.password}@{self.hub_url}"
        
        elif routing_mode == RoutingMode.REGION:
            region = routing_param or "US"
            return f"http://warren-region-{region}:{self.password}@{self.hub_url}"
        
        elif routing_mode == RoutingMode.DEVICE:
            device = routing_param or "primary"
            return f"http://warren+{device}:{self.password}@{self.hub_url}"
        
        elif routing_mode == RoutingMode.GROUP:
            group = routing_param or "residential"
            return f"http://warren-group-{group}:{self.password}@{self.hub_url}"
        
        else:  # RANDOM
            return f"http://warren:{self.password}@{self.hub_url}"


class S5W2CProxyManager:
    """Manages S5W2C Android mobile proxy."""
    
    def __init__(self, phone_ip: str, port: int = 1080):
        """
        Initialize S5W2C proxy manager.
        
        Args:
            phone_ip: Android phone IP address
            port: SOCKS5 port (default 1080)
        """
        self.phone_ip = phone_ip
        self.port = port
    
    def get_proxy(self) -> str:
        """Get S5W2C SOCKS5 proxy URL."""
        return f"socks5://{self.phone_ip}:{self.port}"
    
    def is_available(self) -> bool:
        """Check if S5W2C is available."""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.phone_ip, self.port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"S5W2C availability check failed: {e}")
            return False


class StormsiaBridgeManager:
    """Manages Stormsia free proxy list."""
    
    STORMSIA_BASE = "https://raw.githubusercontent.com/stormsia/proxy-list/main"
    PROTOCOLS = ["socks5", "socks4", "http"]
    CACHE_TTL = 1800  # 30 minutes
    
    def __init__(self, cache_ttl: int = CACHE_TTL):
        """
        Initialize Stormsia bridge.
        
        Args:
            cache_ttl: Cache time-to-live in seconds
        """
        self.cache_ttl = cache_ttl
        self.proxy_cache: Dict[str, List[str]] = {}
        self.cache_timestamp: Dict[str, datetime] = {}
    
    def fetch_proxies(self, protocol: str = "socks5", timeout: int = 10) -> List[str]:
        """
        Fetch proxies from Stormsia.
        
        Args:
            protocol: Proxy protocol (socks5, socks4, http)
            timeout: Request timeout in seconds
        
        Returns:
            List of proxy URLs
        """
        # Check cache
        if protocol in self.proxy_cache:
            if self._is_cache_valid(protocol):
                logger.debug(f"Using cached {protocol} proxies")
                return self.proxy_cache[protocol]
        
        # Fetch fresh list
        url = f"{self.STORMSIA_BASE}/{protocol}.txt"
        try:
            logger.info(f"Fetching {protocol} proxies from Stormsia...")
            with urllib.request.urlopen(url, timeout=timeout) as response:
                content = response.read().decode('utf-8').strip()
                proxies = [p.strip() for p in content.split('\n') if p.strip()]
                
                # Cache result
                self.proxy_cache[protocol] = proxies
                self.cache_timestamp[protocol] = datetime.now()
                
                logger.info(f"✅ Fetched {len(proxies)} {protocol} proxies from Stormsia")
                return proxies
        
        except Exception as e:
            logger.error(f"Failed to fetch {protocol} proxies: {e}")
            # Return cached proxies if available
            if protocol in self.proxy_cache:
                logger.warning(f"Using stale cache for {protocol}")
                return self.proxy_cache[protocol]
            return []
    
    def _is_cache_valid(self, protocol: str) -> bool:
        """Check if cache is still valid."""
        if protocol not in self.cache_timestamp:
            return False
        age = (datetime.now() - self.cache_timestamp[protocol]).total_seconds()
        return age < self.cache_ttl


class AutonomousProxyEngine:
    """
    Autonomous Proxy Engine (APE) — Manages all proxy sources.
    
    Fallback chain:
    1. Warren (residential, primary)
    2. S5W2C (mobile data, secondary)
    3. Stormsia (free, fallback)
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize APE.
        
        Args:
            config: Configuration dict with keys:
                - warren_hub_url: Warren hub URL
                - warren_password: Warren password
                - s5w2c_phone_ip: S5W2C phone IP
                - s5w2c_port: S5W2C port (default 1080)
                - stormsia_cache_ttl: Stormsia cache TTL (default 1800)
        """
        config = config or {}
        
        # Initialize managers
        self.warren_manager = WarrenProxyManager(
            hub_url=config.get("warren_hub_url", os.getenv("WARREN_HUB_URL", "localhost:8000")),
            password=config.get("warren_password", os.getenv("WARREN_PASSWORD", "password"))
        )
        
        self.s5w2c_manager = S5W2CProxyManager(
            phone_ip=config.get("s5w2c_phone_ip", os.getenv("S5W2C_PHONE_IP", "192.168.1.100")),
            port=config.get("s5w2c_port", int(os.getenv("S5W2C_PORT", "1080")))
        )
        
        self.stormsia_manager = StormsiaBridgeManager(
            cache_ttl=config.get("stormsia_cache_ttl", int(os.getenv("STORMSIA_CACHE_TTL", "1800")))
        )
        
        # Metrics tracking
        self.metrics: Dict[str, ProxyMetrics] = {}
        self.failed_proxies: Set[str] = set()
        self.last_rotation = datetime.now()
    
    def get_next_proxy(self, prefer_residential: bool = True, 
                       protocol: str = "socks5") -> Optional[str]:
        """
        Get next proxy with automatic failover.
        
        Fallback chain:
        1. Warren (residential)
        2. S5W2C (mobile)
        3. Stormsia (free)
        
        Args:
            prefer_residential: Prefer residential proxies
            protocol: Protocol for Stormsia fallback (socks5, socks4, http)
        
        Returns:
            Proxy URL or None if all sources fail
        """
        # Try Warren first
        if prefer_residential:
            try:
                proxy = self.warren_manager.get_proxy()
                if proxy and proxy not in self.failed_proxies:
                    logger.debug(f"Using Warren proxy")
                    return proxy
            except Exception as e:
                logger.debug(f"Warren proxy failed: {e}")
        
        # Try S5W2C
        if self.s5w2c_manager.is_available():
            try:
                proxy = self.s5w2c_manager.get_proxy()
                if proxy and proxy not in self.failed_proxies:
                    logger.debug(f"Using S5W2C proxy")
                    return proxy
            except Exception as e:
                logger.debug(f"S5W2C proxy failed: {e}")
        
        # Fall back to Stormsia
        try:
            proxies = self.stormsia_manager.fetch_proxies(protocol=protocol)
            for proxy in proxies:
                if proxy not in self.failed_proxies:
                    logger.debug(f"Using Stormsia {protocol} proxy")
                    return proxy
        except Exception as e:
            logger.debug(f"Stormsia fallback failed: {e}")
        
        logger.error("All proxy sources exhausted")
        return None
    
    def get_sticky_proxy(self, session_id: str) -> Optional[str]:
        """
        Get proxy with sticky session (same device for multi-step flows).
        
        Args:
            session_id: Session identifier
        
        Returns:
            Proxy URL with sticky routing
        """
        proxy = self.warren_manager.get_proxy(
            routing_mode=RoutingMode.STICKY_SESSION,
            routing_param=session_id
        )
        return proxy if proxy not in self.failed_proxies else None
    
    def get_regional_proxy(self, region: str) -> Optional[str]:
        """
        Get proxy from specific region.
        
        Args:
            region: Region code (e.g., "US", "EU", "ASIA")
        
        Returns:
            Proxy URL with regional routing
        """
        proxy = self.warren_manager.get_proxy(
            routing_mode=RoutingMode.REGION,
            routing_param=region
        )
        return proxy if proxy not in self.failed_proxies else None
    
    def mark_proxy_failed(self, proxy: str, duration_seconds: int = 300):
        """
        Mark proxy as failed (skip for N seconds).
        
        Args:
            proxy: Proxy URL
            duration_seconds: Duration to skip (default 5 minutes)
        """
        self.failed_proxies.add(proxy)
        
        # Auto-recovery after duration
        def recover():
            time.sleep(duration_seconds)
            self.failed_proxies.discard(proxy)
            logger.info(f"Proxy recovered: {proxy[:50]}...")
        
        import threading
        threading.Thread(target=recover, daemon=True).start()
    
    def record_success(self, proxy: str, response_time_ms: float = 0.0):
        """Record successful proxy use."""
        if proxy not in self.metrics:
            self.metrics[proxy] = ProxyMetrics(proxy=proxy, proxy_type=ProxyType.WARREN_RESIDENTIAL)
        
        self.metrics[proxy].success_count += 1
        self.metrics[proxy].last_used = datetime.now()
        self.metrics[proxy].avg_response_time_ms = response_time_ms
    
    def record_failure(self, proxy: str):
        """Record failed proxy use."""
        if proxy not in self.metrics:
            self.metrics[proxy] = ProxyMetrics(proxy=proxy, proxy_type=ProxyType.WARREN_RESIDENTIAL)
        
        self.metrics[proxy].failure_count += 1
        self.metrics[proxy].last_failed = datetime.now()
        self.mark_proxy_failed(proxy)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get proxy metrics."""
        return {
            "total_proxies_tracked": len(self.metrics),
            "failed_proxies": len(self.failed_proxies),
            "proxies": {
                proxy: {
                    "success_count": m.success_count,
                    "failure_count": m.failure_count,
                    "success_rate": m.success_rate,
                    "is_healthy": m.is_healthy,
                    "avg_response_time_ms": m.avg_response_time_ms,
                }
                for proxy, m in self.metrics.items()
            }
        }


# Global APE instance
_ape_instance: Optional[AutonomousProxyEngine] = None


def get_ape() -> AutonomousProxyEngine:
    """Get or create global APE instance."""
    global _ape_instance
    if _ape_instance is None:
        _ape_instance = AutonomousProxyEngine()
    return _ape_instance


def init_ape(config: Dict[str, Any]) -> AutonomousProxyEngine:
    """Initialize APE with custom config."""
    global _ape_instance
    _ape_instance = AutonomousProxyEngine(config)
    return _ape_instance
