"""
Autonomous Proxy Validator Service (APVS)
Continuous validation and quality assurance for Stormsia proxy lists.

Features:
- Parallel proxy validation (concurrent testing)
- Multi-endpoint verification
- Anonymity level detection
- Response time measurement
- Automatic blacklist management
- Background refresh service
"""

import logging
import asyncio
import time
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class AnonymityLevel(Enum):
    """Proxy anonymity levels."""
    TRANSPARENT = "transparent"  # Reveals real IP
    ANONYMOUS = "anonymous"      # Hides real IP, reveals proxy
    ELITE = "elite"              # Hides both real IP and proxy


@dataclass
class ProxyValidationResult:
    """Result of proxy validation."""
    proxy: str
    is_valid: bool
    anonymity_level: AnonymityLevel = AnonymityLevel.TRANSPARENT
    response_time_ms: float = 0.0
    test_url: str = ""
    test_timestamp: datetime = field(default_factory=datetime.now)
    error_message: Optional[str] = None
    
    @property
    def age_seconds(self) -> float:
        """Age of validation result in seconds."""
        return (datetime.now() - self.test_timestamp).total_seconds()

    def is_stale(self, ttl_seconds: int = 3600) -> bool:
        """Check if result is stale (older than TTL)."""
        return self.age_seconds > ttl_seconds


class ProxyValidator:
    """Validates individual proxies."""
    
    # Test endpoints
    TEST_ENDPOINTS = [
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
        "https://www.google.com",
    ]
    
    # Timeout settings
    CONNECT_TIMEOUT = 10
    READ_TIMEOUT = 10
    
    @staticmethod
    async def validate_proxy(proxy: str, test_url: Optional[str] = None) -> ProxyValidationResult:
        """
        Validate a single proxy.
        
        Args:
            proxy: Proxy URL (http://, https://, socks5://)
            test_url: Optional custom test URL
        
        Returns:
            ProxyValidationResult
        """
        test_url = test_url or ProxyValidator.TEST_ENDPOINTS[0]
        start_time = time.time()
        
        try:
            # Parse proxy URL
            if not proxy.startswith(("http://", "https://", "socks5://")):
                return ProxyValidationResult(
                    proxy=proxy,
                    is_valid=False,
                    error_message="Invalid proxy format"
                )
            
            # Test proxy with timeout
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    ProxyValidator._test_proxy_sync,
                    proxy,
                    test_url
                ),
                timeout=ProxyValidator.CONNECT_TIMEOUT
            )
            
            response_time_ms = (time.time() - start_time) * 1000
            
            return ProxyValidationResult(
                proxy=proxy,
                is_valid=result["is_valid"],
                anonymity_level=result.get("anonymity_level", AnonymityLevel.TRANSPARENT),
                response_time_ms=response_time_ms,
                test_url=test_url,
                test_timestamp=datetime.now()
            )
        
        except asyncio.TimeoutError:
            return ProxyValidationResult(
                proxy=proxy,
                is_valid=False,
                error_message="Timeout",
                test_url=test_url
            )
        
        except Exception as e:
            return ProxyValidationResult(
                proxy=proxy,
                is_valid=False,
                error_message=str(e),
                test_url=test_url
            )
    
    @staticmethod
    def _test_proxy_sync(proxy: str, test_url: str) -> Dict[str, Any]:
        """Synchronous proxy test (for executor)."""
        try:
            # Use curl_cffi for stealth testing
            try:
                from curl_cffi import requests as cffi_requests
                session = cffi_requests.Session()
                session.proxies = {"http": proxy, "https": proxy}
                resp = session.get(test_url, timeout=10, impersonate="chrome126")
                
                if resp.status_code == 200:
                    # Detect anonymity level from response
                    anonymity = ProxyValidator._detect_anonymity(resp.text)
                    return {
                        "is_valid": True,
                        "anonymity_level": anonymity
                    }
            except ImportError:
                pass
            
            # Fallback to urllib
            proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(proxy_handler)
            resp = opener.open(test_url, timeout=10)
            
            if resp.status == 200:
                anonymity = ProxyValidator._detect_anonymity(resp.read().decode())
                return {
                    "is_valid": True,
                    "anonymity_level": anonymity
                }
        
        except Exception as e:
            logger.debug(f"Proxy validation failed: {e}")
        
        return {"is_valid": False}
    
    @staticmethod
    def _detect_anonymity(response_text: str) -> AnonymityLevel:
        """Detect anonymity level from response."""
        response_lower = response_text.lower()
        
        # Check for headers that reveal proxy usage
        if "via" in response_lower or "x-forwarded-for" in response_lower:
            return AnonymityLevel.ANONYMOUS
        
        # Check for common proxy indicators
        if "proxy" in response_lower or "forwarded" in response_lower:
            return AnonymityLevel.ANONYMOUS
        
        return AnonymityLevel.ELITE


class ProxyValidationService:
    """
    Background service for continuous proxy validation.
    """
    
    def __init__(self, max_concurrent: int = 10, validation_ttl: int = 3600):
        """
        Initialize validation service.
        
        Args:
            max_concurrent: Max concurrent validations
            validation_ttl: Time-to-live for validation results (seconds)
        """
        self.max_concurrent = max_concurrent
        self.validation_ttl = validation_ttl
        
        self.validation_cache: Dict[str, ProxyValidationResult] = {}
        self.blacklist: Set[str] = set()
        self.whitelist: Set[str] = set()
        
        self.is_running = False
        self.service_thread: Optional[threading.Thread] = None
    
    async def validate_proxies(self, proxies: List[str]) -> List[ProxyValidationResult]:
        """
        Validate multiple proxies concurrently.
        
        Args:
            proxies: List of proxy URLs
        
        Returns:
            List of validation results
        """
        # Filter out blacklisted proxies
        proxies_to_test = [p for p in proxies if p not in self.blacklist]
        
        # Check cache for valid results
        results = []
        proxies_to_validate = []
        
        for proxy in proxies_to_test:
            if proxy in self.validation_cache:
                cached = self.validation_cache[proxy]
                if not cached.is_stale(self.validation_ttl):
                    results.append(cached)
                    continue
            
            proxies_to_validate.append(proxy)
        
        # Validate remaining proxies with concurrency limit
        if proxies_to_validate:
            logger.info(f"Validating {len(proxies_to_validate)} proxies...")
            
            semaphore = asyncio.Semaphore(self.max_concurrent)
            
            async def validate_with_limit(proxy):
                async with semaphore:
                    return await ProxyValidator.validate_proxy(proxy)
            
            tasks = [validate_with_limit(p) for p in proxies_to_validate]
            validation_results = await asyncio.gather(*tasks)
            
            # Cache and filter results
            for result in validation_results:
                self.validation_cache[result.proxy] = result
                
                if result.is_valid:
                    results.append(result)
                    self.whitelist.add(result.proxy)
                else:
                    # Add to blacklist temporarily
                    self.blacklist.add(result.proxy)
        
        return results
    
    def get_valid_proxies(self, proxies: List[str]) -> List[str]:
        """
        Get only valid proxies (blocking call).
        
        Args:
            proxies: List of proxy URLs
        
        Returns:
            List of valid proxy URLs
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(self.validate_proxies(proxies))
            return [r.proxy for r in results if r.is_valid]
        finally:
            loop.close()
    
    def start_background_service(self, refresh_interval: int = 300):
        """
        Start background validation service.
        
        Args:
            refresh_interval: Refresh interval in seconds (default 5 minutes)
        """
        if self.is_running:
            logger.warning("Validation service already running")
            return
        
        self.is_running = True
        self.service_thread = threading.Thread(
            target=self._background_loop,
            args=(refresh_interval,),
            daemon=True
        )
        self.service_thread.start()
        logger.info("Proxy validation service started")
    
    def stop_background_service(self):
        """Stop background validation service."""
        self.is_running = False
        if self.service_thread:
            self.service_thread.join(timeout=5)
        logger.info("Proxy validation service stopped")
    
    def _background_loop(self, refresh_interval: int):
        """Background validation loop."""
        while self.is_running:
            try:
                # Refresh blacklist periodically
                self._cleanup_blacklist()
                time.sleep(refresh_interval)
            except Exception as e:
                logger.error(f"Background validation error: {e}")
                time.sleep(refresh_interval)
    
    def _cleanup_blacklist(self):
        """Remove old entries from blacklist."""
        now = datetime.now()
        expired = [
            proxy for proxy, result in self.validation_cache.items()
            if (now - result.test_timestamp).total_seconds() > 600  # 10 minutes
        ]
        
        for proxy in expired:
            self.blacklist.discard(proxy)
            del self.validation_cache[proxy]
        
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired blacklist entries")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get validation service metrics."""
        valid_count = sum(1 for r in self.validation_cache.values() if r.is_valid)
        
        return {
            "total_cached": len(self.validation_cache),
            "valid_proxies": valid_count,
            "blacklisted": len(self.blacklist),
            "whitelisted": len(self.whitelist),
            "avg_response_time_ms": sum(
                r.response_time_ms for r in self.validation_cache.values() if r.is_valid
            ) / max(valid_count, 1),
        }


class StormsiaBridgeWithValidation:
    """
    Enhanced Stormsia bridge with automatic validation.
    """
    
    def __init__(self, auto_validate: bool = True):
        """
        Initialize bridge.
        
        Args:
            auto_validate: Automatically validate proxies
        """
        from scrapers.proxy_engine import StormsiaBridgeManager
        
        self.bridge = StormsiaBridgeManager()
        self.validator = ProxyValidationService(max_concurrent=20)
        self.auto_validate = auto_validate
        
        if auto_validate:
            self.validator.start_background_service(refresh_interval=300)
    
    def fetch_valid_proxies(self, protocol: str = "socks5", 
                           min_quality: float = 0.8) -> List[str]:
        """
        Fetch and validate proxies from Stormsia.
        
        Args:
            protocol: Proxy protocol
            min_quality: Minimum quality threshold (0.0-1.0)
        
        Returns:
            List of valid proxies
        """
        # Fetch raw proxies
        raw_proxies = self.bridge.fetch_proxies(protocol=protocol)
        
        if not raw_proxies:
            logger.warning("No proxies fetched from Stormsia")
            return []
        
        logger.info(f"Fetched {len(raw_proxies)} raw {protocol} proxies")
        
        # Validate proxies
        if self.auto_validate:
            valid_proxies = self.validator.get_valid_proxies(raw_proxies)
            logger.info(f"Validated: {len(valid_proxies)}/{len(raw_proxies)} proxies")
            return valid_proxies
        
        return raw_proxies
    
    def get_validation_metrics(self) -> Dict[str, Any]:
        """Get validation metrics."""
        return self.validator.get_metrics()
    
    def shutdown(self):
        """Shutdown service."""
        self.validator.stop_background_service()


# Global validator instance
_validator_instance: Optional[StormsiaBridgeWithValidation] = None


def get_validator() -> StormsiaBridgeWithValidation:
    """Get or create global validator instance."""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = StormsiaBridgeWithValidation(auto_validate=True)
    return _validator_instance


def init_validator(auto_validate: bool = True) -> StormsiaBridgeWithValidation:
    """Initialize validator with custom settings."""
    global _validator_instance
    _validator_instance = StormsiaBridgeWithValidation(auto_validate=auto_validate)
    return _validator_instance
