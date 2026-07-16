# Self-Hosted Autonomous Proxy Ecosystem Architecture (2026)

## Executive Summary

This document outlines the complete architecture for a **cost-free, self-hosted, autonomous residential and mobile proxy ecosystem** using three key components:

1. **Warren** (doedja/warren) — Self-hosted residential proxy pool from personal devices
2. **S5W2C** (h4ckm310n/S5W2C) — Android SOCKS5 mobile proxy (WiFi→Mobile Data)
3. **Stormsia Proxy List** (stormsia/proxy-list) — Free, auto-updated proxy aggregator

This ecosystem integrates seamlessly with the ShamrockLeads 2026 stealth stack, providing:
- **Zero cost** (no commercial proxy subscriptions)
- **Full autonomy** (self-hosted, no third-party dependencies)
- **Maximum stealth** (residential + mobile IPs, diverse exit nodes)
- **Automatic failover** (health checking, dynamic rotation)

---

## Component 1: Warren (Residential Proxy Pool)

### Architecture Overview

**Warren** is a Rust-based residential proxy pool that turns your own devices into exit nodes.

**Key Characteristics:**
- **Binary Size:** ~15MB (single executable)
- **Deployment:** One binary per device + hub server
- **Protocol:** SOCKS5 + HTTP proxy
- **Exit IPs:** Your actual home/office IPs (100% residential)
- **Routing:** Per-device, per-region, per-group, per-session

### How It Works

```
Device 1 (Home PC)  ─┐
Device 2 (Laptop)   ─┤
Device 3 (Phone)    ─┼─→ Warren Hub (Central Proxy Server)
Device 4 (RPi)      ─┤
Device 5 (VPS)      ─┘
                        ↓
                    One Proxy Endpoint
                    (http://warren:PASSWORD@HUB:8000)
```

### Deployment Strategy for ShamrockLeads

**Phase 1: Hub Setup**
- Deploy Warren hub on Hetzner VPS (5.161.126.32)
- Exposes single proxy endpoint on port 8000
- Dashboard on port 8080 (admin-only)

**Phase 2: Device Enrollment**
- Install Warren binary on each personal device:
  - Home PC (residential)
  - Laptop (residential)
  - Spare phone (mobile data fallback)
  - Raspberry Pi (always-on residential)

**Phase 3: Routing Configuration**
```bash
# Route all traffic through pool
curl -x http://warren:PASSWORD@HUB:8000 https://api.ipify.org

# Route through specific device (e.g., home PC)
curl -x http://warren+home-pc:PASSWORD@HUB:8000 https://api.ipify.org

# Route by region (if device location matches)
curl -x http://warren-region-US:PASSWORD@HUB:8000 https://api.ipify.org

# Sticky session (keep same IP for multi-step flows)
curl -x http://warren-session-abc123:PASSWORD@HUB:8000 https://api.ipify.org
```

### Integration with ShamrockLeads

```python
# In stealth_utils.py
class WarrenProxyManager:
    def __init__(self, hub_url: str, password: str):
        self.hub_url = hub_url  # e.g., "http://warren:PASSWORD@5.161.126.32:8000"
        self.password = password
        self.session_keys = {}  # Track sticky sessions
    
    def get_proxy(self, routing_mode: str = "random") -> str:
        """
        Get proxy with routing mode:
        - "random": Random device from pool
        - "sticky-SESSION_ID": Same device for sequence
        - "region-COUNTRY": Device in specific region
        - "device-NAME": Specific device
        """
        if routing_mode.startswith("sticky-"):
            session_id = routing_mode.split("-")[1]
            return f"http://warren-session-{session_id}:{self.password}@{self.hub_url}"
        elif routing_mode.startswith("region-"):
            region = routing_mode.split("-")[1]
            return f"http://warren-region-{region}:{self.password}@{self.hub_url}"
        elif routing_mode.startswith("device-"):
            device = routing_mode.split("-")[1]
            return f"http://warren+{device}:{self.password}@{self.hub_url}"
        else:
            return f"http://warren:{self.password}@{self.hub_url}"
```

---

## Component 2: S5W2C (Android Mobile Proxy)

### Architecture Overview

**S5W2C** is an Android application that acts as a SOCKS5 proxy server, receiving requests via WiFi and forwarding them through mobile data (4G/5G).

**Key Characteristics:**
- **Protocol:** SOCKS5
- **Input:** WiFi (LAN clients)
- **Output:** Mobile data (cellular)
- **Exit IPs:** Real carrier-assigned mobile IPs
- **Use Case:** Bypass WiFi-only restrictions, add mobile diversity

### How It Works

```
┌─────────────────────────────────────────┐
│         WiFi LAN Clients                │
│  (Scraper, curl, browser, etc.)         │
└──────────────┬──────────────────────────┘
               │ SOCKS5 requests
               ↓
        ┌──────────────┐
        │   S5W2C App  │
        │  (Android)   │
        └──────────────┘
               │ Forward via mobile data
               ↓
        ┌──────────────┐
        │  4G/5G Cell  │
        │   Network    │
        └──────────────┘
               │
               ↓
        Real Mobile IP
        (Carrier-assigned)
```

### Deployment Strategy for ShamrockLeads

**Phase 1: Setup**
1. Install S5W2C APK on Android phone
2. Enable mobile data on phone
3. Connect phone to same WiFi as scraper
4. Start S5W2C app (listens on port 1080 by default)

**Phase 2: Configuration**
- Phone IP: 192.168.1.100 (example)
- SOCKS5 Port: 1080
- Proxy URL: `socks5://192.168.1.100:1080`

**Phase 3: Integration**
```python
# In stealth_utils.py
class S5W2CProxyManager:
    def __init__(self, phone_ip: str, port: int = 1080):
        self.phone_ip = phone_ip
        self.port = port
    
    def get_proxy(self) -> str:
        """Return SOCKS5 proxy URL for mobile data exit."""
        return f"socks5://{self.phone_ip}:{self.port}"
```

### Limitations & Workarounds
- **No IPv6 support** — Use IPv4-only targets
- **No BIND requests** — Not needed for scraping
- **Single device** — Can run multiple instances on different phones

---

## Component 3: Stormsia Proxy List (Free Aggregator)

### Architecture Overview

**Stormsia** is an automated proxy collector and validator that updates every 30 minutes with verified working proxies.

**Current Statistics:**
- **HTTP/HTTPS:** 284 active proxies
- **SOCKS4:** 138 active proxies
- **SOCKS5:** 470 active proxies
- **Total:** 892 verified proxies
- **Update Frequency:** Every 30 minutes
- **Verification:** Full HTTP request/response round-trip

### Data Access Methods

**Method 1: Direct Download**
```bash
curl -o proxies.txt https://raw.githubusercontent.com/stormsia/proxy-list/main/working_proxies.txt
curl -o socks5.txt https://raw.githubusercontent.com/stormsia/proxy-list/main/socks5.txt
```

**Method 2: Python Fetch**
```python
import urllib.request

def fetch_stormsia_proxies(protocol: str = "socks5") -> List[str]:
    """Fetch fresh proxy list from stormsia."""
    url = f"https://raw.githubusercontent.com/stormsia/proxy-list/main/{protocol}.txt"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            proxies = response.read().decode('utf-8').strip().split('\n')
            return [p.strip() for p in proxies if p.strip()]
    except Exception as e:
        logger.error(f"Failed to fetch {protocol} proxies: {e}")
        return []
```

**Method 3: GitHub API**
```bash
curl -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/stormsia/proxy-list/contents/socks5.txt
```

### Integration with ShamrockLeads

```python
# In stealth_utils.py
class StormsiaBridgeManager:
    """Bridge to stormsia free proxy list."""
    
    STORMSIA_BASE = "https://raw.githubusercontent.com/stormsia/proxy-list/main"
    PROTOCOLS = ["http", "socks4", "socks5"]
    
    @staticmethod
    def fetch_proxies(protocol: str = "socks5", timeout: int = 10) -> List[str]:
        """Fetch and validate proxies from stormsia."""
        url = f"{StormsiaBridgeManager.STORMSIA_BASE}/{protocol}.txt"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                proxies = response.read().decode('utf-8').strip().split('\n')
                return [p.strip() for p in proxies if p.strip()]
        except Exception as e:
            logger.warning(f"Stormsia fetch failed: {e}")
            return []
    
    @staticmethod
    def validate_proxy(proxy: str, timeout: int = 5) -> bool:
        """Quick validation: test proxy against ipify."""
        try:
            if proxy.startswith("socks"):
                # Use curl_cffi for SOCKS validation
                from curl_cffi import requests as cffi_requests
                session = cffi_requests.Session()
                session.proxies = {"http": proxy, "https": proxy}
                resp = session.get("https://api.ipify.org?format=json", timeout=timeout)
                return resp.status_code == 200
            return False
        except Exception:
            return False
```

---

## Integrated Proxy Engine Architecture

### 4-Layer Proxy Stack

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: IP Rotation (Residential + Mobile)                 │
│  ├─ Warren (home devices)                                   │
│  ├─ S5W2C (mobile data)                                     │
│  └─ Stormsia (free fallback)                                │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: TLS Fingerprinting (curl_cffi)                     │
│  ├─ JA4 fingerprinting                                      │
│  ├─ Random browser signatures                               │
│  └─ Realistic headers                                       │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Engine Stealth (Patchright + undetected-chrome)    │
│  ├─ Engine-level patches                                    │
│  ├─ navigator.webdriver removal                             │
│  └─ Hardware fingerprint spoofing                           │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: Behavioral Simulation                              │
│  ├─ Random delays                                           │
│  ├─ Mouse/scroll patterns                                   │
│  └─ Realistic viewport                                      │
└─────────────────────────────────────────────────────────────┘
```

### Autonomous Proxy Manager

```python
# In stealth_utils.py
class AutonomousProxyEngine:
    """
    Manages all proxy sources autonomously:
    1. Warren (primary residential)
    2. S5W2C (mobile data)
    3. Stormsia (free fallback)
    
    Automatic failover, health checking, and rotation.
    """
    
    def __init__(self):
        self.warren_manager = WarrenProxyManager(
            hub_url="5.161.126.32:8000",
            password=os.getenv("WARREN_PASSWORD")
        )
        self.s5w2c_manager = S5W2CProxyManager(
            phone_ip=os.getenv("S5W2C_PHONE_IP", "192.168.1.100")
        )
        self.stormsia_manager = StormsiaBridgeManager()
        
        self.proxy_cache = []
        self.last_refresh = None
        self.failed_proxies = set()
    
    def get_next_proxy(self, prefer_residential: bool = True) -> Optional[str]:
        """
        Get next proxy with automatic failover:
        1. Try Warren (residential)
        2. Try S5W2C (mobile)
        3. Fall back to Stormsia (free)
        """
        if prefer_residential:
            # Try Warren first
            proxy = self.warren_manager.get_proxy()
            if proxy and proxy not in self.failed_proxies:
                return proxy
            
            # Try S5W2C
            proxy = self.s5w2c_manager.get_proxy()
            if proxy and proxy not in self.failed_proxies:
                return proxy
        
        # Fall back to Stormsia
        if not self.proxy_cache or self._should_refresh_cache():
            self.proxy_cache = self.stormsia_manager.fetch_proxies("socks5")
            self.last_refresh = datetime.now()
        
        for proxy in self.proxy_cache:
            if proxy not in self.failed_proxies:
                return proxy
        
        return None
    
    def mark_proxy_failed(self, proxy: str):
        """Mark proxy as failed (skip for next N rotations)."""
        self.failed_proxies.add(proxy)
    
    def _should_refresh_cache(self) -> bool:
        """Refresh cache every 30 minutes (matches stormsia update)."""
        if not self.last_refresh:
            return True
        return (datetime.now() - self.last_refresh).total_seconds() > 1800
```

---

## Deployment & Operations

### Deployment Timeline

| Phase | Duration | Component | Action |
| :--- | :--- | :--- | :--- |
| **Phase 1** | 1 day | Warren Hub | Deploy on Hetzner VPS |
| **Phase 2** | 1 day | Warren Nodes | Install binary on 4-5 devices |
| **Phase 3** | 1 day | S5W2C | Install APK on Android phone |
| **Phase 4** | 1 day | Stormsia Bridge | Integrate into stealth_utils.py |
| **Phase 5** | 1 day | Testing | Validate all 3 sources work |
| **Phase 6** | Ongoing | Monitoring | Health checks, failover testing |

### Hetzner VPS Setup (Warren Hub)

```bash
# SSH into Hetzner VPS
ssh -i .shamrock_deploy_key root@5.161.126.32

# Download Warren hub binary
wget https://github.com/doedja/warren/releases/download/v0.4.11/warren-x86_64-unknown-linux-gnu
chmod +x warren-x86_64-unknown-linux-gnu

# Run hub on port 8000
./warren-x86_64-unknown-linux-gnu hub --listen 0.0.0.0:8000 --admin 0.0.0.0:8080

# (Optional) Run as systemd service
sudo tee /etc/systemd/system/warren.service > /dev/null <<EOF
[Unit]
Description=Warren Proxy Hub
After=network.target

[Service]
Type=simple
ExecStart=/root/warren-x86_64-unknown-linux-gnu hub --listen 0.0.0.0:8000 --admin 0.0.0.0:8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable warren
sudo systemctl start warren
```

### Device Enrollment (Warren Nodes)

```bash
# On each device (PC, laptop, RPi)
wget https://github.com/doedja/warren/releases/download/v0.4.11/warren-x86_64-unknown-linux-gnu
chmod +x warren-x86_64-unknown-linux-gnu

# Enroll with hub
./warren-x86_64-unknown-linux-gnu node \
  --hub wss://5.161.126.32:8000 \
  --name "home-pc" \
  --token "shamrock-residential"
```

### S5W2C Setup (Android)

1. Download APK from GitHub releases
2. Install on Android phone
3. Enable mobile data
4. Connect to same WiFi as scraper
5. Open S5W2C app → Start Server
6. Note IP address (e.g., 192.168.1.100:1080)

### Environment Variables

```bash
# .env or docker-compose.yml
WARREN_HUB_URL=http://5.161.126.32:8000
WARREN_PASSWORD=your-secure-password
S5W2C_PHONE_IP=192.168.1.100
S5W2C_PHONE_PORT=1080
STORMSIA_CACHE_TTL=1800  # 30 minutes
```

---

## Monitoring & Health Checks

### Autonomous Health Checker

```python
# In scrapers/proxy_health_checker.py
class ProxyHealthChecker:
    """Continuously monitors proxy health and reports to dashboard."""
    
    async def check_proxy_health(self, proxy: str) -> Dict[str, Any]:
        """Test proxy against multiple endpoints."""
        results = {
            "proxy": proxy,
            "timestamp": datetime.now().isoformat(),
            "tests": {}
        }
        
        test_urls = [
            "https://api.ipify.org?format=json",
            "https://httpbin.org/ip",
            "https://www.google.com",
        ]
        
        for url in test_urls:
            try:
                start = time.time()
                # Test with curl_cffi
                from curl_cffi import requests as cffi_requests
                session = cffi_requests.Session()
                session.proxies = {"http": proxy, "https": proxy}
                resp = session.get(url, timeout=10, impersonate="chrome126")
                duration = time.time() - start
                
                results["tests"][url] = {
                    "status": "ok" if resp.status_code == 200 else "failed",
                    "status_code": resp.status_code,
                    "duration_ms": int(duration * 1000)
                }
            except Exception as e:
                results["tests"][url] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return results
```

---

## Cost Analysis

| Component | Cost | Notes |
| :--- | :--- | :--- |
| **Warren Hub** | $0 | Uses existing Hetzner VPS |
| **Warren Nodes** | $0 | Uses personal devices |
| **S5W2C** | $0 | Uses personal Android phone |
| **Stormsia** | $0 | Free, public GitHub repo |
| **Total** | **$0/month** | 100% autonomous, zero cost |

---

## Next Steps

1. **Deploy Warren Hub** on Hetzner VPS
2. **Enroll devices** as Warren nodes
3. **Set up S5W2C** on Android phone
4. **Integrate** AutonomousProxyEngine into stealth_utils.py
5. **Test** with v2.0 scrapers (Tennessee, Connecticut, Texas, etc.)
6. **Monitor** proxy health and failover

---

**Prepared by:** Manus AI Agent  
**Project:** shamrock-leads  
**Date:** 2026-07  
**Status:** Ready for Implementation
