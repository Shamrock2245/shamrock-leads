"""
Autonomous Proxy Engine (APE) Integration Test Suite
Tests all components: Warren, S5W2C, Stormsia, and full scraper integration.
"""

import unittest
import asyncio
import logging
from unittest.mock import Mock, patch, MagicMock
from scrapers.proxy_validator import AnonymityLevel
from datetime import datetime, timedelta

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class TestWarrenProxyManager(unittest.TestCase):
    """Test Warren residential proxy manager."""
    
    def setUp(self):
        from scrapers.proxy_engine import WarrenProxyManager
        self.manager = WarrenProxyManager(
            hub_url="localhost:8000",
            password="test-password"
        )
    
    def test_get_proxy_random(self):
        """Test random proxy generation."""
        proxy = self.manager.get_proxy()
        self.assertIn("warren", proxy)
        self.assertIn("localhost:8000", proxy)
        self.assertIn("test-password", proxy)
    
    def test_get_proxy_sticky_session(self):
        """Test sticky session proxy generation."""
        from scrapers.proxy_engine import RoutingMode
        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.STICKY_SESSION,
            routing_param="session-123"
        )
        self.assertIn("warren-session-session-123", proxy)
    
    def test_get_proxy_regional(self):
        """Test regional proxy generation."""
        from scrapers.proxy_engine import RoutingMode
        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.REGION,
            routing_param="US"
        )
        self.assertIn("warren-region-US", proxy)
    
    def test_get_proxy_device(self):
        """Test device-specific proxy generation."""
        from scrapers.proxy_engine import RoutingMode
        proxy = self.manager.get_proxy(
            routing_mode=RoutingMode.DEVICE,
            routing_param="laptop-1"
        )
        self.assertIn("warren+laptop-1", proxy)


class TestS5W2CProxyManager(unittest.TestCase):
    """Test S5W2C mobile proxy manager."""
    
    def setUp(self):
        from scrapers.proxy_engine import S5W2CProxyManager
        self.manager = S5W2CProxyManager(
            phone_ip="192.168.1.100",
            port=1080
        )
    
    def test_get_proxy(self):
        """Test S5W2C proxy URL generation."""
        proxy = self.manager.get_proxy()
        self.assertEqual(proxy, "socks5://192.168.1.100:1080")
    
    @patch("socket.socket")
    def test_is_available_true(self, mock_socket):
        """Test S5W2C availability check (available)."""
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 0
        mock_socket.return_value = mock_sock_instance
        
        result = self.manager.is_available()
        self.assertTrue(result)
    
    @patch("socket.socket")
    def test_is_available_false(self, mock_socket):
        """Test S5W2C availability check (unavailable)."""
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 1
        mock_socket.return_value = mock_sock_instance
        
        result = self.manager.is_available()
        self.assertFalse(result)


class TestStormsiaBridgeManager(unittest.TestCase):
    """Test Stormsia free proxy list manager."""
    
    def setUp(self):
        from scrapers.proxy_engine import StormsiaBridgeManager
        self.manager = StormsiaBridgeManager(cache_ttl=60)
    
    def test_cache_validity(self):
        """Test cache validity check."""
        from datetime import datetime
        
        # Fresh cache should be valid
        self.manager.cache_timestamp["socks5"] = datetime.now()
        self.manager.proxy_cache["socks5"] = ["proxy1", "proxy2"]
        
        result = self.manager._is_cache_valid("socks5")
        self.assertTrue(result)
    
    def test_cache_expiry(self):
        """Test cache expiry."""
        from datetime import datetime
        
        # Old cache should be invalid
        old_time = datetime.now() - timedelta(seconds=120)
        self.manager.cache_timestamp["socks5"] = old_time
        self.manager.proxy_cache["socks5"] = ["proxy1", "proxy2"]
        
        result = self.manager._is_cache_valid("socks5")
        self.assertFalse(result)


class TestProxyValidator(unittest.TestCase):
    """Test proxy validation service."""
    
    def setUp(self):
        from scrapers.proxy_validator import ProxyValidator, ProxyValidationResult, AnonymityLevel
        self.validator = ProxyValidator
        self.result_class = ProxyValidationResult

    def test_validation_result_creation(self):
        """Test basic creation of ProxyValidationResult."""
        result = self.result_class(
            proxy="http://test.com:8080",
            is_valid=True,
            anonymity_level=AnonymityLevel.ELITE,
            response_time_ms=150.0,
            test_url="https://api.ipify.org"
        )
        self.assertEqual(result.proxy, "http://test.com:8080")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.anonymity_level, ProxyValidator.AnonymityLevel.ELITE)
        self.assertAlmostEqual(result.response_time_ms, 150.0)
        self.assertEqual(result.test_url, "https://api.ipify.org")
        self.assertIsNotNone(result.test_timestamp)
        self.assertFalse(result.is_stale(ttl_seconds=0)) # Should not be stale immediately

    



class TestAutonomousProxyEngine(unittest.TestCase):
    """Test main APE class."""
    
    def setUp(self):
        from scrapers.proxy_engine import AutonomousProxyEngine
        self.ape = AutonomousProxyEngine({
            "warren_hub_url": "localhost:8000",
            "warren_password": "test",
            "s5w2c_phone_ip": "192.168.1.100",
        })
    
    def test_get_next_proxy_warren(self):
        """Test proxy selection from Warren."""
        proxy = self.ape.get_next_proxy(prefer_residential=True)
        self.assertIsNotNone(proxy)
        self.assertIn("localhost:8000", proxy)
    
    def test_get_sticky_proxy(self):
        """Test sticky session proxy."""
        proxy = self.ape.get_sticky_proxy("session-123")
        self.assertIsNotNone(proxy)
        self.assertIn("session-123", proxy)
    
    def test_get_regional_proxy(self):
        """Test regional proxy."""
        proxy = self.ape.get_regional_proxy("US")
        self.assertIsNotNone(proxy)
        self.assertIn("region-US", proxy)
    
    def test_mark_proxy_failed(self):
        """Test proxy failure marking."""
        proxy = "http://test:8080"
        self.ape.mark_proxy_failed(proxy, duration_seconds=1)
        
        self.assertIn(proxy, self.ape.failed_proxies)
    
    def test_record_success(self):
        """Test success recording."""
        proxy = "http://test:8080"
        self.ape.record_success(proxy, response_time_ms=100.0)
        
        self.assertIn(proxy, self.ape.metrics)
        self.assertEqual(self.ape.metrics[proxy].success_count, 1)
    
    def test_record_failure(self):
        """Test failure recording."""
        proxy = "http://test:8080"
        self.ape.record_failure(proxy)
        
        self.assertIn(proxy, self.ape.metrics)
        self.assertEqual(self.ape.metrics[proxy].failure_count, 1)
    
    def test_get_metrics(self):
        """Test metrics collection."""
        proxy = "http://test:8080"
        self.ape.record_success(proxy, response_time_ms=100.0)
        
        metrics = self.ape.get_metrics()
        self.assertIn("total_proxies_tracked", metrics)
        self.assertIn("failed_proxies", metrics)
        self.assertGreater(metrics["total_proxies_tracked"], 0)


class TestStealthUtilsIntegration(unittest.TestCase):
    """Test stealth_utils APE integration."""
    
    def test_get_autonomous_proxy_engine(self):
        """Test APE retrieval from stealth_utils."""
        from scrapers.stealth_utils import get_autonomous_proxy_engine
        ape = get_autonomous_proxy_engine()
        self.assertIsNotNone(ape)
    
    def test_get_proxy_with_stealth(self):
        """Test stealth proxy retrieval."""
        from scrapers.stealth_utils import get_proxy_with_stealth
        proxy = get_proxy_with_stealth(prefer_residential=True)
        # May be None if APE not initialized, but should not raise
        self.assertTrue(proxy is None or isinstance(proxy, str))


class TestBasScraperIntegration(unittest.TestCase):
    """Test BaseScraper APE integration."""
    
    def setUp(self):
        from scrapers.base_scraper import BaseScraper
        
        class TestScraper(BaseScraper):
            @property
            def county(self):
                return "Test"
            
            def scrape(self):
                return []
        
        self.scraper = TestScraper()
    
    def test_scraper_ape_initialization(self):
        """Test that scraper initializes APE."""
        self.assertIsNotNone(self.scraper.ape)
    
    def test_get_proxy(self):
        """Test proxy retrieval from scraper."""
        proxy = self.scraper.get_proxy()
        # May be None, but should not raise
        self.assertTrue(proxy is None or isinstance(proxy, str))
    
    def test_get_sticky_proxy(self):
        """Test sticky proxy retrieval."""
        proxy = self.scraper.get_sticky_proxy("session-123")
        self.assertTrue(proxy is None or isinstance(proxy, str))
    
    def test_record_proxy_success(self):
        """Test success recording."""
        self.scraper.record_proxy_success("http://test:8080", 100.0)
        # Should not raise
    
    def test_record_proxy_failure(self):
        """Test failure recording."""
        self.scraper.record_proxy_failure("http://test:8080")
        # Should not raise


class TestEndToEndScenarios(unittest.TestCase):
    """Test end-to-end scraping scenarios."""
    
    def test_failover_chain(self):
        """Test complete failover chain."""
        from scrapers.proxy_engine import AutonomousProxyEngine
        
        ape = AutonomousProxyEngine()
        
        # Should attempt Warren first
        proxy1 = ape.get_next_proxy(prefer_residential=True)
        self.assertIsNotNone(proxy1)
        
        # Mark as failed
        ape.mark_proxy_failed(proxy1)
        
        # Should attempt next source
        proxy2 = ape.get_next_proxy(prefer_residential=True)
        # May be same or different depending on configuration
        self.assertTrue(proxy2 is None or isinstance(proxy2, str))
    
    def test_metrics_tracking(self):
        """Test metrics tracking across requests."""
        from scrapers.proxy_engine import AutonomousProxyEngine
        
        ape = AutonomousProxyEngine()
        
        # Simulate successful requests
        proxy1 = "http://proxy1:8080"
        proxy2 = "http://proxy2:8080"
        
        ape.record_success(proxy1, response_time_ms=100.0)
        ape.record_success(proxy1, response_time_ms=110.0)
        ape.record_failure(proxy2)
        
        metrics = ape.get_metrics()
        
        self.assertEqual(metrics["total_proxies_tracked"], 2)
        self.assertEqual(metrics["failed_proxies"], 1)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestWarrenProxyManager))
    suite.addTests(loader.loadTestsFromTestCase(TestS5W2CProxyManager))
    suite.addTests(loader.loadTestsFromTestCase(TestStormsiaBridgeManager))
    suite.addTests(loader.loadTestsFromTestCase(TestProxyValidator))
    suite.addTests(loader.loadTestsFromTestCase(TestAutonomousProxyEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStealthUtilsIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestBasScraperIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestEndToEndScenarios))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)
