"""
2026 Stealthiest Stack — Multi-layer evasion utilities.

Implements the 4-layer stealth architecture:
1. IP Layer — Residential/mobile proxy rotation
2. TLS Layer — curl_cffi with JA4 fingerprinting
3. Engine Layer — Patchright (Playwright stealth), undetected-chromedriver
4. Behavior Layer — Random delays, mouse/scroll simulation, realistic patterns

References:
- curl_cffi: TLS/JA3 fingerprinting (https://github.com/yifeikong/curl_cffi)
- Patchright: Maintained Playwright stealth successor (https://github.com/Kalibrr/patchright)
- undetected-chromedriver: Engine-level Chrome patches (https://github.com/ultrafunkamsterdam/undetected-chromedriver)
- nodriver: CDP-direct stealth browsing (https://github.com/ultrafunkamsterdam/nodriver)
"""

import logging
import random
import time
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ProxyRotator:
    """
    Manages residential/mobile proxy rotation for maximum stealth.
    Supports multiple proxy providers (Bright Data, Oxylabs, Smartproxy, etc.).
    """
    
    def __init__(self, proxy_list: Optional[List[str]] = None):
        """
        Initialize proxy rotator.
        
        Args:
            proxy_list: List of proxy URLs (e.g., ["http://proxy1:8080", "http://proxy2:8080"])
        """
        self.proxy_list = proxy_list or []
        self.current_index = 0
        self.failed_proxies = set()
    
    def get_next_proxy(self) -> Optional[str]:
        """
        Get next proxy in rotation, skipping failed ones.
        """
        if not self.proxy_list:
            return None
        
        attempts = 0
        while attempts < len(self.proxy_list):
            proxy = self.proxy_list[self.current_index % len(self.proxy_list)]
            self.current_index += 1
            
            if proxy not in self.failed_proxies:
                return proxy
            
            attempts += 1
        
        # All proxies failed, reset
        self.failed_proxies.clear()
        return self.proxy_list[0] if self.proxy_list else None
    
    def mark_failed(self, proxy: str):
        """
        Mark a proxy as failed (will be skipped for next N rotations).
        """
        self.failed_proxies.add(proxy)
    
    def get_curl_cffi_proxy(self) -> Optional[Dict[str, str]]:
        """
        Get proxy in curl_cffi format.
        """
        proxy = self.get_next_proxy()
        if proxy:
            return {"http": proxy, "https": proxy}
        return None


class TLSFingerprinter:
    """
    Manages TLS fingerprinting via curl_cffi.
    Impersonates real browser TLS signatures (JA4 fingerprinting).
    """
    
    # Valid TLS signatures supported by curl_cffi 0.15+
    CHROME_SIGNATURES = [
        "chrome124",
        "chrome120",
        "chrome110",
        "chrome",
    ]
    
    SAFARI_SIGNATURES = [
        "safari15_5",
        "safari",
    ]
    
    @staticmethod
    def get_random_signature() -> str:
        """
        Get random browser signature for TLS impersonation.
        Rotates between Chrome and Safari to avoid pattern detection.
        """
        if random.random() < 0.85:  # 85% Chrome, 15% Safari
            return random.choice(TLSFingerprinter.CHROME_SIGNATURES)
        return random.choice(TLSFingerprinter.SAFARI_SIGNATURES)
    
    @staticmethod
    def get_curl_cffi_headers() -> Dict[str, str]:
        """
        Get realistic headers for curl_cffi requests.
        """
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        ]
        
        return {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": random.choice([
                "en-US,en;q=0.9",
                "en-US,en;q=0.9,es;q=0.8",
                "en-US,en;q=0.9,fr;q=0.8",
            ]),
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }


class BehaviorSimulator:
    """
    Simulates realistic human behavior to bypass behavioral analysis.
    Includes random delays, mouse movements, scroll simulation, etc.
    """
    
    @staticmethod
    def random_delay(min_secs: float = 0.5, max_secs: float = 3.0):
        """
        Random delay with realistic distribution (not uniform).
        Skews towards shorter delays with occasional longer pauses.
        """
        if random.random() < 0.1:  # 10% chance of longer pause
            delay = random.uniform(max_secs, max_secs * 2)
        else:
            delay = random.uniform(min_secs, max_secs)
        
        time.sleep(delay)
    
    @staticmethod
    async def async_random_delay(min_secs: float = 0.5, max_secs: float = 3.0):
        """
        Async version of random_delay.
        """
        if random.random() < 0.1:
            delay = random.uniform(max_secs, max_secs * 2)
        else:
            delay = random.uniform(min_secs, max_secs)
        
        await asyncio.sleep(delay)
    
    @staticmethod
    def get_realistic_viewport() -> Dict[str, int]:
        """
        Get realistic viewport dimensions.
        Matches real user distribution (not uniform).
        """
        # Common desktop resolutions
        resolutions = [
            (1920, 1080),
            (1366, 768),
            (1440, 900),
            (1536, 864),
            (1280, 720),
            (1600, 900),
        ]
        
        width, height = random.choice(resolutions)
        
        # Add slight randomization
        width += random.randint(-50, 50)
        height += random.randint(-30, 30)
        
        return {"width": width, "height": height}
    
    @staticmethod
    def get_realistic_accept_language() -> str:
        """
        Get realistic Accept-Language header.
        """
        languages = [
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,es;q=0.8",
            "en-US,en;q=0.9,fr;q=0.8",
            "en-US,en;q=0.9,de;q=0.8",
            "en-US,en;q=0.9,it;q=0.8",
            "en-US,en;q=0.9,pt;q=0.8",
        ]
        return random.choice(languages)
    
    @staticmethod
    def get_realistic_timezone() -> str:
        """
        Get realistic timezone (US-focused for bail bonds).
        """
        timezones = [
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Los_Angeles",
            "America/Anchorage",
            "Pacific/Honolulu",
        ]
        return random.choice(timezones)


class PatchrightBrowserManager:
    """
    Manages Patchright (Playwright stealth successor) for engine-level evasion.
    """
    
    @staticmethod
    async def create_stealth_browser(proxy: Optional[str] = None):
        """
        Create a Patchright browser with full stealth configuration.
        
        Args:
            proxy: Proxy URL (optional)
        
        Returns:
            Browser instance with stealth patches applied
        """
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise ImportError("patchright not installed. Run: pip install patchright")
        
        pw = await async_playwright().__aenter__()
        
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
        }
        
        if proxy:
            launch_args["proxy"] = {"server": proxy}
        
        browser = await pw.chromium.launch(**launch_args)
        
        logger.info("✅ Patchright stealth browser created")
        return pw, browser
    
    @staticmethod
    async def create_stealth_context(browser, proxy: Optional[str] = None):
        """
        Create a stealth browser context with realistic device profile.
        """
        viewport = BehaviorSimulator.get_realistic_viewport()
        
        context = await browser.new_context(
            viewport=viewport,
            user_agent=TLSFingerprinter.get_curl_cffi_headers()["User-Agent"],
            locale="en-US",
            timezone_id=BehaviorSimulator.get_realistic_timezone(),
            geolocation={"latitude": 40.7128, "longitude": -74.0060},  # NYC default
            permissions=["geolocation"],
            ignore_https_errors=True,
        )
        
        # Inject stealth JavaScript
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = window.chrome || {};
            window.chrome.runtime = window.chrome.runtime || {};
        """)
        
        return context


class UndetectedChromeManager:
    """
    Manages undetected-chromedriver for engine-level Chrome patches.
    """
    
    @staticmethod
    async def create_undetected_browser(proxy: Optional[str] = None, headless: bool = True):
        """
        Create an undetected Chrome browser with engine-level patches.
        
        Args:
            proxy: Proxy URL (optional)
            headless: Run in headless mode
        
        Returns:
            Browser instance with anti-detection patches
        """
        try:
            import undetected_chromedriver as uc
        except ImportError:
            raise ImportError("undetected-chromedriver not installed. Run: pip install undetected-chromedriver")
        
        options = uc.ChromeOptions()
        
        if headless:
            options.add_argument("--headless=new")
        
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
        
        # Randomized viewport
        viewport = BehaviorSimulator.get_realistic_viewport()
        options.add_argument(f"--window-size={viewport['width']},{viewport['height']}")
        
        browser = await uc.start(options=options)
        
        logger.info("✅ Undetected Chrome browser created")
        return browser


class CurlCFFISession:
    """
    Manages curl_cffi sessions with full stealth configuration.
    """
    
    @staticmethod
    def create_session(proxy: Optional[str] = None, proxy_rotator: Optional[ProxyRotator] = None):
        """
        Create a curl_cffi session with TLS fingerprinting and proxy rotation.
        
        Args:
            proxy: Single proxy URL (optional)
            proxy_rotator: ProxyRotator instance for rotation (optional)
        
        Returns:
            Configured curl_cffi session
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed. Run: pip install curl_cffi")
        
        session = cffi_requests.Session()
        
        # Set proxy
        if proxy_rotator:
            proxy_dict = proxy_rotator.get_curl_cffi_proxy()
            if proxy_dict:
                session.proxies = proxy_dict
        elif proxy:
            session.proxies = {"http": proxy, "https": proxy}
        
        return session
    
    @staticmethod
    def make_request(
        session,
        url: str,
        method: str = "GET",
        proxy_rotator: Optional[ProxyRotator] = None,
        timeout: int = 15,
        **kwargs
    ):
        """
        Make a curl_cffi request with full stealth configuration.
        
        Args:
            session: curl_cffi session
            url: Target URL
            method: HTTP method (GET, POST, etc.)
            proxy_rotator: ProxyRotator for rotation
            timeout: Request timeout
            **kwargs: Additional arguments for curl_cffi
        
        Returns:
            Response object
        """
        headers = TLSFingerprinter.get_curl_cffi_headers()
        signature = TLSFingerprinter.get_random_signature()
        
        # Add random delay before request
        BehaviorSimulator.random_delay(0.5, 2.0)
        
        try:
            if method.upper() == "GET":
                resp = session.get(
                    url,
                    headers=headers,
                    impersonate=signature,
                    timeout=timeout,
                    **kwargs
                )
            elif method.upper() == "POST":
                resp = session.post(
                    url,
                    headers=headers,
                    impersonate=signature,
                    timeout=timeout,
                    **kwargs
                )
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            resp.raise_for_status()
            return resp
        
        except Exception as e:
            if proxy_rotator:
                proxy = proxy_rotator.get_next_proxy()
                if proxy:
                    proxy_rotator.mark_failed(proxy)
                    logger.warning(f"Proxy failed, rotating: {e}")
            raise


class StealthConfig:
    """
    Centralized stealth configuration for all scrapers.
    """
    
    def __init__(self):
        self.proxy_rotator: Optional[ProxyRotator] = None
        self.use_patchright = True
        self.use_undetected_chrome = True
        self.use_curl_cffi = True
        self.behavioral_simulation = True
        self.random_delay_range = (0.5, 3.0)
    
    def set_proxies(self, proxy_list: List[str]):
        """
        Set proxy list for rotation.
        """
        self.proxy_rotator = ProxyRotator(proxy_list)
    
    def get_proxy(self) -> Optional[str]:
        """
        Get next proxy from rotator.
        """
        if self.proxy_rotator:
            return self.proxy_rotator.get_next_proxy()
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Export configuration as dictionary.
        """
        return {
            "use_patchright": self.use_patchright,
            "use_undetected_chrome": self.use_undetected_chrome,
            "use_curl_cffi": self.use_curl_cffi,
            "behavioral_simulation": self.behavioral_simulation,
            "random_delay_range": self.random_delay_range,
            "has_proxies": bool(self.proxy_rotator and self.proxy_rotator.proxy_list),
        }


# Global stealth config instance
_stealth_config = StealthConfig()


def get_stealth_config() -> StealthConfig:
    """
    Get global stealth configuration.
    """
    return _stealth_config


# ============================================================================
# Autonomous Proxy Engine Integration
# ============================================================================

def get_autonomous_proxy_engine():
    """
    Get Autonomous Proxy Engine (APE) for unified proxy management.
    Integrates Warren, S5W2C, and Stormsia.
    
    Returns:
        AutonomousProxyEngine instance
    """
    try:
        from scrapers.proxy_engine import get_ape
        return get_ape()
    except ImportError:
        logger.warning("proxy_engine module not available")
        return None


def get_proxy_with_stealth(prefer_residential: bool = True) -> Optional[str]:
    """
    Get proxy with full stealth configuration.
    Combines APE proxy selection with TLS fingerprinting.
    
    Args:
        prefer_residential: Prefer residential proxies
    
    Returns:
        Proxy URL with stealth headers configured
    """
    ape = get_autonomous_proxy_engine()
    if not ape:
        return None
    
    proxy = ape.get_next_proxy(prefer_residential=prefer_residential)
    return proxy


def make_stealth_request(url: str, method: str = "GET", 
                        prefer_residential: bool = True,
                        timeout: int = 15, **kwargs):
    """
    Make HTTP request with full stealth stack:
    - Autonomous proxy selection (Warren/S5W2C/Stormsia)
    - TLS fingerprinting (curl_cffi)
    - Behavioral simulation (delays, headers)
    
    Args:
        url: Target URL
        method: HTTP method
        prefer_residential: Prefer residential proxies
        timeout: Request timeout
        **kwargs: Additional curl_cffi arguments
    
    Returns:
        Response object or None on failure
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.error("curl_cffi not installed")
        return None
    
    ape = get_autonomous_proxy_engine()
    proxy = ape.get_next_proxy(prefer_residential=prefer_residential) if ape else None
    
    session = cffi_requests.Session()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    
    headers = TLSFingerprinter.get_curl_cffi_headers()
    signature = TLSFingerprinter.get_random_signature()
    
    # Add behavioral delay
    if get_stealth_config().behavioral_simulation:
        BehaviorSimulator.random_delay(*get_stealth_config().random_delay_range)
    
    try:
        if method.upper() == "GET":
            resp = session.get(
                url,
                headers=headers,
                impersonate=signature,
                timeout=timeout,
                **kwargs
            )
        elif method.upper() == "POST":
            resp = session.post(
                url,
                headers=headers,
                impersonate=signature,
                timeout=timeout,
                **kwargs
            )
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        resp.raise_for_status()
        
        # Record success in APE
        if ape and proxy:
            ape.record_success(proxy, response_time_ms=resp.elapsed.total_seconds() * 1000)
        
        return resp
    
    except Exception as e:
        # Record failure in APE
        if ape and proxy:
            ape.record_failure(proxy)
        
        logger.error(f"Stealth request failed: {e}")
        raise
    
    finally:
        session.close()
