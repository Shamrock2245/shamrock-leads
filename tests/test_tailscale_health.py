import asyncio
import pytest
from unittest.mock import MagicMock, patch

from config.tailscale import TailscaleConfig
from dashboard.services.tailscale_health import TailscaleHealthMonitor, PeerStatus


@pytest.fixture
def mock_ts_config():
    cfg = TailscaleConfig()
    cfg.imac_ip = "100.102.10.86"
    cfg.imac_hostname = "shamrocksimac"
    cfg.bb_port = 1234
    cfg.socks_port = 1080
    return cfg


def test_tailscale_config_defaults(mock_ts_config):
    assert mock_ts_config.enabled is True
    assert mock_ts_config.imac_ip == "100.102.10.86"
    assert "100.102.10.86:1234" in mock_ts_config.bb_url_tailscale
    assert "1080" in mock_ts_config.imac_socks_url


def test_bb_url_failover_order(mock_ts_config):
    # Case 1: Tailscale iMac reachable
    with patch.object(mock_ts_config, "is_imac_reachable", return_value=True):
        url = mock_ts_config.get_bb_url_with_fallback("http://custom:1234")
        assert "100.102.10.86:1234" in url

    # Case 2: Tailscale down, frp responds
    with patch.object(mock_ts_config, "is_imac_reachable", return_value=False):
        with patch.object(mock_ts_config, "_tcp_probe", return_value=True):
            url = mock_ts_config.get_bb_url_with_fallback()
            assert "178.156.179.237:12434" in url


@pytest.mark.asyncio
async def test_peer_check_bluebubbles_alive_even_if_ssh_closed(mock_ts_config):
    monitor = TailscaleHealthMonitor()
    monitor.config = mock_ts_config

    peer = PeerStatus(
        hostname="shamrocksimac",
        ip="100.102.10.86",
        services={"bluebubbles": False, "socks": False, "ssh": False},
    )

    # Simulate: Port 22 (SSH) is closed, but port 1234 (BlueBubbles) is open
    def mock_probe(host, port, timeout):
        if port == 1234:
            return True
        return False

    with patch.object(mock_ts_config, "_tcp_probe", side_effect=mock_probe):
        with patch.object(monitor, "_measure_latency", return_value=24.5):
            await monitor._check_peer(peer)

    # Peer must be marked reachable because BlueBubbles responded!
    assert peer.reachable is True
    assert peer.services["bluebubbles"] is True
    assert peer.services["ssh"] is False
    assert peer.latency_ms == 24.5
    assert peer.consecutive_failures == 0


def test_get_best_bb_url_priorities(mock_ts_config):
    monitor = TailscaleHealthMonitor()
    monitor.config = mock_ts_config

    # Case 1: When Tailscale BB service is active
    monitor._peers["imac"].services["bluebubbles"] = True
    assert "100.102.10.86:1234" in monitor.get_best_bb_url()

    # Case 2: When Tailscale BB service is down, frp responds
    monitor._peers["imac"].services["bluebubbles"] = False
    with patch.object(mock_ts_config, "is_imac_reachable", return_value=False):
        with patch.object(mock_ts_config, "_tcp_probe", return_value=True):
            assert "178.156.179.237:12434" in monitor.get_best_bb_url()


def test_status_dict(mock_ts_config):
    monitor = TailscaleHealthMonitor()
    monitor.config = mock_ts_config

    status = monitor.status_dict()
    assert "enabled" in status
    assert "peers" in status
    assert "imac" in status["peers"]
    assert status["peers"]["imac"]["ip"] == "100.102.10.86"
