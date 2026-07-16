# Autonomous Proxy Engine (APE) Integration Guide

## Overview

The **Autonomous Proxy Engine (APE)** integrates Warren, S5W2C, and Stormsia into a unified, self-healing proxy management system for ShamrockLeads scrapers.

**Key Features:**
- ✅ Automatic failover (Warren → S5W2C → Stormsia)
- ✅ Health checking and metrics tracking
- ✅ Sticky session routing for multi-step flows
- ✅ Regional routing support
- ✅ Zero-cost, fully autonomous

---

## Architecture

### Proxy Fallback Chain

```
Request → Warren (residential) → S5W2C (mobile) → Stormsia (free) → Fail
```

### Integration Points

```
┌──────────────────────────────────────────┐
│         ShamrockLeads Scraper            │
│    (tennessee_tncis_v2.py, etc.)         │
└────────────────┬─────────────────────────┘
                 │
                 ↓
┌──────────────────────────────────────────┐
│    Autonomous Proxy Engine (APE)         │
│    (proxy_engine.py)                     │
├──────────────────────────────────────────┤
│ • WarrenProxyManager                     │
│ • S5W2CProxyManager                      │
│ • StormsiaBridgeManager                  │
│ • Metrics & Health Checking              │
└────────────────┬─────────────────────────┘
                 │
      ┌──────────┼──────────┐
      ↓          ↓          ↓
  ┌────────┐ ┌────────┐ ┌─────────┐
  │ Warren │ │ S5W2C  │ │Stormsia │
  │ Hub    │ │ Phone  │ │ GitHub  │
  └────────┘ └────────┘ └─────────┘
```

---

## Setup & Configuration

### 1. Environment Variables

Create `.env` file in project root:

```bash
# Warren Configuration
WARREN_HUB_URL=5.161.126.32:8000
WARREN_PASSWORD=your-secure-password

# S5W2C Configuration
S5W2C_PHONE_IP=192.168.1.100
S5W2C_PORT=1080

# Stormsia Configuration
STORMSIA_CACHE_TTL=1800  # 30 minutes

# Scraper Configuration
PROXY_PREFER_RESIDENTIAL=true
PROXY_PROTOCOL=socks5
```

### 2. Docker Compose Integration

```yaml
version: '3.8'

services:
  scraper:
    image: shamrock-leads:latest
    environment:
      WARREN_HUB_URL: warren-hub:8000
      WARREN_PASSWORD: ${WARREN_PASSWORD}
      S5W2C_PHONE_IP: ${S5W2C_PHONE_IP}
      S5W2C_PORT: 1080
      STORMSIA_CACHE_TTL: 1800
    networks:
      - shamrock
    depends_on:
      - warren-hub

  warren-hub:
    image: doedja/warren:latest
    ports:
      - "8000:8000"
      - "8080:8080"
    networks:
      - shamrock
    environment:
      WARREN_LISTEN: 0.0.0.0:8000
      WARREN_ADMIN: 0.0.0.0:8080

networks:
  shamrock:
    driver: bridge
```

---

## Usage Examples

### Basic Usage (Auto Failover)

```python
from scrapers.proxy_engine import get_ape

# Get global APE instance
ape = get_ape()

# Get next proxy (auto failover)
proxy = ape.get_next_proxy()
print(f"Using proxy: {proxy}")

# Use with curl_cffi
from curl_cffi import requests as cffi_requests
session = cffi_requests.Session()
session.proxies = {"http": proxy, "https": proxy}
resp = session.get("https://api.ipify.org")
```

### Sticky Session (Multi-Step Flows)

```python
from scrapers.proxy_engine import get_ape

ape = get_ape()

# Get sticky proxy for multi-step flow
session_id = "court-search-abc123"
proxy = ape.get_sticky_proxy(session_id)

# All requests in this session use same IP
# Step 1: Login
resp1 = session.get("https://court.example.com/login", proxies={"http": proxy})

# Step 2: Search (same IP)
resp2 = session.post("https://court.example.com/search", proxies={"http": proxy})

# Step 3: Download (same IP)
resp3 = session.get("https://court.example.com/download", proxies={"http": proxy})
```

### Regional Routing

```python
from scrapers.proxy_engine import get_ape

ape = get_ape()

# Get proxy from specific region (if Warren nodes available)
proxy_us = ape.get_regional_proxy("US")
proxy_eu = ape.get_regional_proxy("EU")

# Use for geo-specific scraping
resp = session.get(url, proxies={"http": proxy_us})
```

### Error Handling & Failover

```python
from scrapers.proxy_engine import get_ape
import time

ape = get_ape()

max_retries = 3
for attempt in range(max_retries):
    proxy = ape.get_next_proxy()
    
    try:
        resp = session.get(url, proxies={"http": proxy}, timeout=10)
        
        # Record success
        ape.record_success(proxy, response_time_ms=resp.elapsed.total_seconds() * 1000)
        break
    
    except Exception as e:
        # Record failure
        ape.record_failure(proxy)
        logger.warning(f"Proxy failed (attempt {attempt+1}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff
```

### Metrics & Monitoring

```python
from scrapers.proxy_engine import get_ape
import json

ape = get_ape()

# Get metrics
metrics = ape.get_metrics()
print(json.dumps(metrics, indent=2))

# Output:
# {
#   "total_proxies_tracked": 15,
#   "failed_proxies": 2,
#   "proxies": {
#     "http://warren:pass@hub:8000": {
#       "success_count": 42,
#       "failure_count": 1,
#       "success_rate": 0.977,
#       "is_healthy": true,
#       "avg_response_time_ms": 245.3
#     },
#     ...
#   }
# }
```

---

## Integration with Scrapers

### Tennessee Scraper v2.0 Example

```python
# In tennessee_tncis_v2.py

from scrapers.proxy_engine import get_ape
from scrapers.stealth_utils import CurlCFFISession, BehaviorSimulator

class TennesseeTnCISScraperV2(BaseScraper):
    def scrape(self) -> List[ArrestRecord]:
        ape = get_ape()
        
        # Get proxy with auto failover
        proxy = ape.get_next_proxy(prefer_residential=True)
        
        if not proxy:
            logger.error("No proxies available")
            return []
        
        try:
            # Create session with proxy
            session = CurlCFFISession.create_session(proxy=proxy)
            
            # Make request
            resp = CurlCFFISession.make_request(
                session,
                BASE_URL,
                method="GET",
                timeout=20
            )
            
            # Record success
            ape.record_success(proxy, response_time_ms=resp.elapsed.total_seconds() * 1000)
            
            # Parse and return records
            records = self._parse_response(resp)
            return records
        
        except Exception as e:
            # Record failure and try next proxy
            ape.record_failure(proxy)
            logger.error(f"Scrape failed with proxy {proxy}: {e}")
            
            # Retry with different proxy
            return self.scrape()
        
        finally:
            session.close()
```

---

## Deployment Checklist

- [ ] **Warren Hub**: Deployed on Hetzner VPS (5.161.126.32:8000)
- [ ] **Warren Nodes**: Enrolled 4-5 personal devices
- [ ] **S5W2C**: Installed on Android phone, connected to WiFi
- [ ] **Environment Variables**: Configured in `.env`
- [ ] **APE Module**: Integrated into `stealth_utils.py`
- [ ] **Scrapers**: Updated to use `get_ape()`
- [ ] **Testing**: Validated proxy rotation with test scraper
- [ ] **Monitoring**: Set up metrics dashboard

---

## Troubleshooting

### Warren Hub Not Responding

```bash
# SSH into Hetzner VPS
ssh -i .shamrock_deploy_key root@5.161.126.32

# Check service status
sudo systemctl status warren

# View logs
sudo journalctl -u warren -f

# Restart service
sudo systemctl restart warren
```

### S5W2C Not Available

```bash
# Check phone connectivity
ping 192.168.1.100

# Test SOCKS5 connection
curl -x socks5://192.168.1.100:1080 https://api.ipify.org

# Restart S5W2C app on phone
# (Open app → Menu → Restart Server)
```

### Stormsia Fetch Failing

```python
# Check Stormsia availability
from scrapers.proxy_engine import StormsiaBridgeManager

manager = StormsiaBridgeManager()
proxies = manager.fetch_proxies("socks5")
print(f"Fetched {len(proxies)} proxies")
```

### Proxy Metrics Degrading

```python
from scrapers.proxy_engine import get_ape

ape = get_ape()
metrics = ape.get_metrics()

# Find unhealthy proxies
for proxy, data in metrics["proxies"].items():
    if not data["is_healthy"]:
        print(f"Unhealthy: {proxy} (success_rate: {data['success_rate']})")
```

---

## Performance Tuning

### Optimize for Speed

```python
# Use Warren with sticky sessions (fastest)
proxy = ape.get_sticky_proxy("scraper-session-1")
```

### Optimize for Stealth

```python
# Rotate through all sources
proxy = ape.get_next_proxy(prefer_residential=False)
```

### Optimize for Reliability

```python
# Prefer residential, auto failover
proxy = ape.get_next_proxy(prefer_residential=True)
```

---

## Advanced Configuration

### Custom Proxy Manager

```python
from scrapers.proxy_engine import AutonomousProxyEngine, WarrenProxyManager

# Create custom APE instance
custom_ape = AutonomousProxyEngine({
    "warren_hub_url": "custom-hub.example.com:8000",
    "warren_password": "custom-password",
    "s5w2c_phone_ip": "10.0.0.50",
    "stormsia_cache_ttl": 3600,
})

# Use custom instance
proxy = custom_ape.get_next_proxy()
```

### Health Check Interval

```python
# Customize health check frequency
ape.stormsia_manager.cache_ttl = 600  # 10 minutes instead of 30
```

---

## Monitoring Dashboard

Create a simple monitoring dashboard:

```python
# In dashboard.py
from scrapers.proxy_engine import get_ape
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/api/proxy-metrics")
def proxy_metrics():
    ape = get_ape()
    return jsonify(ape.get_metrics())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Access at: `http://localhost:5000/api/proxy-metrics`

---

## References

- **Warren**: https://github.com/doedja/warren
- **S5W2C**: https://github.com/h4ckm310n/S5W2C
- **Stormsia**: https://github.com/stormsia/proxy-list

---

**Prepared by:** Manus AI Agent  
**Project:** shamrock-leads  
**Date:** 2026-07  
**Status:** Ready for Deployment
