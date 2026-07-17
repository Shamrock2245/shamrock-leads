"""
Autonomous Proxy Engine (APE) Integration Test Suite
Tests Warren, S5W2C, Stormsia, and BaseScraper helpers.
"""

import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class TestWarrenProxyManager(unittest.TestCase):
    """Test Warren residential proxy manager."""

    def setUp(self):
        from scrapers.proxy_engine import WarrenProxyManager

        self.manager = WarrenProxyManager(
            hub_url="localhost:8000",
            password="test-password",
        )

    def test_get_proxy_random(self):
        proxy = self.manager.get_proxy()
        self.assertIsNotNone(proxy)
        self.assertIn("warren", proxy)
        self.assertIn("localhost:8000", proxy)
        self.assertIn("test-password", proxy)

    def test_get_proxy_sticky_session(self):
        from scrapers.proxy_engine import RoutingMode

        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.STICKY_SESSION,
            routing_param="session-123",
        )
        self.assertIn("warren-session-session-123", proxy)

    def test_get_proxy_regional(self):
        from scrapers.proxy_engine import RoutingMode

        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.REGION,
            routing_param="US",
        )
        self.assertIn("warren-region-US", proxy)

    def test_get_proxy_device(self):
        from scrapers.proxy_engine import RoutingMode

        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.DEVICE,
            routing_param="laptop-1",
        )
        self.assertIn("warren+laptop-1", proxy)

    def test_placeholder_password_not_configured(self):
        from scrapers.proxy_engine import WarrenProxyManager

        m = WarrenProxyManager(hub_url="localhost:8000", password="password")
        self.assertFalse(m.is_configured)
        self.assertIsNone(m.get_proxy())


class TestS5W2CProxyManager(unittest.TestCase):
    """Test S5W2C mobile proxy manager."""

    def setUp(self):
        from scrapers.proxy_engine import S5W2CProxyManager

        self.manager = S5W2CProxyManager(phone_ip="10.0.0.50", port=1080)

    def test_get_proxy(self):
        proxy = self.manager.get_proxy()
        self.assertEqual(proxy, "socks5://10.0.0.50:1080")

    def test_placeholder_not_configured(self):
        from scrapers.proxy_engine import S5W2CProxyManager

        m = S5W2CProxyManager(phone_ip="192.168.1.100")
        self.assertFalse(m.is_configured)
        self.assertIsNone(m.get_proxy())

    @patch("socket.socket")
    def test_is_available_true(self, mock_socket):
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 0
        mock_socket.return_value = mock_sock_instance

        self.assertTrue(self.manager.is_available())

    @patch("socket.socket")
    def test_is_available_false(self, mock_socket):
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 1
        mock_socket.return_value = mock_sock_instance

        self.assertFalse(self.manager.is_available())


class TestStormsiaBridgeManager(unittest.TestCase):
    """Test Stormsia free proxy list manager."""

    def setUp(self):
        from scrapers.proxy_engine import StormsiaBridgeManager

        self.manager = StormsiaBridgeManager(cache_ttl=60)

    def test_cache_validity(self):
        self.manager.cache_timestamp["socks5"] = datetime.now()
        self.manager.proxy_cache["socks5"] = ["socks5://proxy1:1080", "socks5://proxy2:1080"]
        self.assertTrue(self.manager._is_cache_valid("socks5"))

    def test_cache_expiry(self):
        old_time = datetime.now() - timedelta(seconds=120)
        self.manager.cache_timestamp["socks5"] = old_time
        self.manager.proxy_cache["socks5"] = ["socks5://proxy1:1080"]
        self.assertFalse(self.manager._is_cache_valid("socks5"))

    def test_next_proxy_round_robin(self):
        self.manager.cache_timestamp["socks5"] = datetime.now()
        self.manager.proxy_cache["socks5"] = [
            "socks5://a:1",
            "socks5://b:2",
            "socks5://c:3",
        ]
        p1 = self.manager.next_proxy("socks5")
        p2 = self.manager.next_proxy("socks5")
        self.assertNotEqual(p1, p2)
        self.assertIn(p1, self.manager.proxy_cache["socks5"])


class TestProxyValidator(unittest.TestCase):
    """Test proxy validation result helpers."""

    def test_validation_result_creation(self):
        from scrapers.proxy_validator import AnonymityLevel, ProxyValidationResult

        result = ProxyValidationResult(
            proxy="http://test.com:8080",
            is_valid=True,
            anonymity_level=AnonymityLevel.ELITE,
            response_time_ms=150.0,
            test_url="https://api.ipify.org",
        )
        self.assertEqual(result.proxy, "http://test.com:8080")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.anonymity_level, AnonymityLevel.ELITE)
        self.assertAlmostEqual(result.response_time_ms, 150.0)
        self.assertEqual(result.test_url, "https://api.ipify.org")
        self.assertIsNotNone(result.test_timestamp)
        # Fresh result is not stale with a positive TTL
        self.assertFalse(result.is_stale(ttl_seconds=3600))
        # Zero TTL means anything with age >= 0 is stale
        self.assertTrue(result.is_stale(ttl_seconds=0))


class TestAutonomousProxyEngine(unittest.TestCase):
    """Test main APE class."""

    def setUp(self):
        from scrapers.proxy_engine import AutonomousProxyEngine

        self.ape = AutonomousProxyEngine(
            {
                "warren_hub_url": "localhost:8000",
                "warren_password": "real-secret-password",
                "s5w2c_phone_ip": "10.0.0.50",
                "warren_enabled": True,
                "s5w2c_enabled": True,
                "stormsia_enabled": True,
            }
        )

    def test_get_next_proxy_warren(self):
        with patch.object(self.ape.warren_manager, "is_available", return_value=True):
            proxy = self.ape.get_next_proxy(prefer_residential=True)
        self.assertIsNotNone(proxy)
        self.assertIn("localhost:8000", proxy)

    def test_get_sticky_proxy(self):
        with patch.object(self.ape.warren_manager, "is_available", return_value=True):
            proxy = self.ape.get_sticky_proxy("session-123")
        self.assertIsNotNone(proxy)
        self.assertIn("session-123", proxy)

    def test_get_regional_proxy(self):
        with patch.object(self.ape.warren_manager, "is_available", return_value=True):
            proxy = self.ape.get_regional_proxy("US")
        self.assertIsNotNone(proxy)
        self.assertIn("region-US", proxy)

    def test_mark_proxy_failed(self):
        proxy = "http://test:8080"
        self.ape.mark_proxy_failed(proxy, duration_seconds=1)
        self.assertIn(proxy, self.ape.failed_proxies)

    def test_record_success(self):
        proxy = "http://test:8080"
        self.ape.record_success(proxy, response_time_ms=100.0)
        self.assertIn(proxy, self.ape.metrics)
        self.assertEqual(self.ape.metrics[proxy].success_count, 1)

    def test_record_failure(self):
        proxy = "http://test:8080"
        self.ape.record_failure(proxy)
        self.assertIn(proxy, self.ape.metrics)
        self.assertEqual(self.ape.metrics[proxy].failure_count, 1)

    def test_get_metrics(self):
        proxy = "http://test:8080"
        self.ape.record_success(proxy, response_time_ms=100.0)
        metrics = self.ape.get_metrics()
        self.assertIn("total_proxies_tracked", metrics)
        self.assertIn("failed_proxies", metrics)
        self.assertIn("sources", metrics)
        self.assertGreater(metrics["total_proxies_tracked"], 0)


class TestStealthUtilsIntegration(unittest.TestCase):
    """Test stealth_utils APE integration."""

    def test_get_autonomous_proxy_engine(self):
        from scrapers.stealth_utils import get_autonomous_proxy_engine

        ape = get_autonomous_proxy_engine()
        self.assertIsNotNone(ape)

    def test_get_proxy_with_stealth(self):
        from scrapers.stealth_utils import get_proxy_with_stealth

        proxy = get_proxy_with_stealth(prefer_residential=True)
        self.assertTrue(proxy is None or isinstance(proxy, str))


class TestBasScraperIntegration(unittest.TestCase):
    """Test BaseScraper APE integration."""

    def setUp(self):
        from scrapers.base_scraper import BaseScraper
        from scrapers.proxy_engine import init_ape

        # Deterministic APE for tests (no real Warren required)
        init_ape(
            {
                "warren_hub_url": "localhost:8000",
                "warren_password": "real-secret-password",
                "s5w2c_phone_ip": "",
                "warren_enabled": False,
                "s5w2c_enabled": False,
                "stormsia_enabled": True,
            }
        )

        class TestScraper(BaseScraper):
            @property
            def county(self):
                return "Test"

            def scrape(self):
                return []

        self.scraper = TestScraper()

    def test_scraper_ape_initialization(self):
        self.assertIsNotNone(self.scraper.ape)

    def test_get_proxy(self):
        proxy = self.scraper.get_proxy()
        self.assertTrue(proxy is None or isinstance(proxy, str))

    def test_get_sticky_proxy(self):
        proxy = self.scraper.get_sticky_proxy("session-123")
        self.assertTrue(proxy is None or isinstance(proxy, str))

    def test_record_proxy_success(self):
        self.scraper.record_proxy_success("http://test:8080", 100.0)

    def test_record_proxy_failure(self):
        self.scraper.record_proxy_failure("http://test:8080")


class TestEndToEndScenarios(unittest.TestCase):
    """Test end-to-end scraping scenarios."""

    def test_failover_to_stormsia(self):
        """When Warren/S5W2C are off, Stormsia still supplies proxies."""
        from scrapers.proxy_engine import AutonomousProxyEngine

        ape = AutonomousProxyEngine(
            {
                "warren_enabled": False,
                "s5w2c_enabled": False,
                "stormsia_enabled": True,
            }
        )
        ape.stormsia_manager.proxy_cache["socks5"] = [
            "socks5://127.0.0.1:9050",
            "socks5://127.0.0.1:9051",
        ]
        ape.stormsia_manager.cache_timestamp["socks5"] = datetime.now()
        proxy = ape.get_next_proxy(prefer_residential=True)
        self.assertIsNotNone(proxy)
        self.assertTrue(proxy.startswith(("socks5://", "socks4://", "http://")))

        ape.mark_proxy_failed(proxy)
        proxy2 = ape.get_next_proxy(prefer_residential=True)
        self.assertTrue(proxy2 is None or isinstance(proxy2, str))
        if proxy2:
            self.assertNotEqual(proxy, proxy2)

    def test_metrics_tracking(self):
        from scrapers.proxy_engine import AutonomousProxyEngine

        ape = AutonomousProxyEngine({"warren_enabled": False, "s5w2c_enabled": False})
        proxy1 = "http://proxy1:8080"
        proxy2 = "http://proxy2:8080"

        ape.record_success(proxy1, response_time_ms=100.0)
        ape.record_success(proxy1, response_time_ms=110.0)
        ape.record_failure(proxy2)

        metrics = ape.get_metrics()
        self.assertEqual(metrics["total_proxies_tracked"], 2)
        self.assertEqual(metrics["failed_proxies"], 1)
        # Rolling average should be between samples
        avg = metrics["proxies"][proxy1]["avg_response_time_ms"]
        self.assertGreaterEqual(avg, 100.0)
        self.assertLessEqual(avg, 110.0)


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestWarrenProxyManager,
        TestS5W2CProxyManager,
        TestStormsiaBridgeManager,
        TestProxyValidator,
        TestAutonomousProxyEngine,
        TestStealthUtilsIntegration,
        TestBasScraperIntegration,
        TestEndToEndScenarios,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    raise SystemExit(0 if success else 1)
