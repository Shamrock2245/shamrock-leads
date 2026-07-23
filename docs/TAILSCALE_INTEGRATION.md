# Tailscale Integration — ShamrockLeads Mesh Network

> **Status:** ACTIVE  
> **Last Updated:** 2026-07-23  
> **Tailnet:** `shamrockbailbonds.biz`

---

## Executive Summary

Tailscale provides a zero-config WireGuard mesh that replaces the previous patchwork of ngrok tunnels, frp TCP proxies, and SSH reverse tunnels. All inter-node communication now rides encrypted peer-to-peer connections with automatic NAT traversal, MagicDNS, and ACL-based access control.

---

## Network Topology

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TAILNET: shamrockbailbonds.biz                        │
│                    (WireGuard encrypted mesh)                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────────────┐     ┌──────────────────────┐                 │
│  │  HETZNER VPS         │     │  OFFICE iMAC         │                 │
│  │  shamrock-vps        │◄───►│  shamrocksimac       │                 │
│  │  100.x.x.x           │     │  100.102.10.86       │                 │
│  │                      │     │                      │                 │
│  │  Services:           │     │  Services:           │                 │
│  │  • Scraper Engine    │     │  • BlueBubbles :1234 │                 │
│  │  • Dashboard :5050   │     │  • SOCKS5 :1080      │                 │
│  │  • OSINT Worker      │     │  • SSH :22           │                 │
│  │  • Node-RED :1880    │     │  • Exit Node (res IP)│                 │
│  │  • Traccar :8082     │     │                      │                 │
│  │  • MongoDB (Atlas)   │     │  LAN: 10.1.10.52    │                 │
│  │  • Warren Hub :8000  │     │  WAN: 96.79.229.158 │                 │
│  └──────────────────────┘     └──────────────────────┘                 │
│           ▲                              ▲                              │
│           │                              │                              │
│           ▼                              ▼                              │
│  ┌──────────────────────┐                                              │
│  │  LAPTOP              │                                              │
│  │  shamrock-laptop     │                                              │
│  │  100.x.x.x           │                                              │
│  │                      │                                              │
│  │  Dev/Ops access to:  │                                              │
│  │  • VPS Dashboard     │                                              │
│  │  • iMac SSH          │                                              │
│  │  • Node-RED Editor   │                                              │
│  └──────────────────────┘                                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## What Tailscale Replaces

| Before (Legacy) | After (Tailscale) | Benefit |
|-----------------|-------------------|---------|
| ngrok static domain → BlueBubbles | `http://shamrocksimac:1234` direct | No relay, no interstitial headers, lower latency |
| frp TCP proxy (VPS:12434 → iMac:1234) | Direct peer-to-peer | No frps/frpc to maintain, no token rotation |
| SSH -R reverse tunnel (iMac → VPS:1080) | `socks5://shamrocksimac:1080` | Always-on, no SSH keepalive issues |
| SSH -p 12222 via frp for iMac access | `ssh shamrockbailbonds@shamrocksimac` | Direct, no port mapping |
| Public IP + port forwards | MagicDNS hostnames | No firewall rules, no IP changes |
| ngrok `ngrok-skip-browser-warning` header | Not needed | Clean HTTP, no interstitial |

---

## Failover Architecture

The system maintains **graceful degradation** — if Tailscale is down, it falls back to legacy paths automatically:

```
BlueBubbles Connection Priority:
  1. Tailscale direct  → http://shamrocksimac:1234    (preferred)
  2. ngrok static      → https://pseudospherical-etta-untactually.ngrok-free.dev
  3. frp TCP           → http://178.156.179.237:12434 (legacy)

Proxy/Residential IP Priority:
  1. Tailscale SOCKS   → socks5://shamrocksimac:1080  (residential exit)
  2. Warren Hub        → socks5://178.156.179.237:8000
  3. Direct            → no proxy (datacenter IP)
```

---

## Configuration

### Environment Variables

```env
# Master switch — set to false to disable all Tailscale routing
TAILSCALE_ENABLED=true

# Auth key for Docker sidecar (generate at login.tailscale.com/admin/settings/keys)
TAILSCALE_AUTHKEY=tskey-auth-...

# Tailnet domain
TAILSCALE_TAILNET=shamrockbailbonds.biz

# Device hostnames (MagicDNS)
TAILSCALE_IMAC_HOSTNAME=shamrocksimac
TAILSCALE_VPS_HOSTNAME=shamrock-vps
TAILSCALE_LAPTOP_HOSTNAME=shamrock-laptop

# Known IPs (fallback if MagicDNS fails)
TAILSCALE_IMAC_IP=100.102.10.86

# Exit node for residential IP routing
TAILSCALE_EXIT_NODE=shamrocksimac
```

### Docker Compose

Add the Tailscale sidecar to your compose stack:

```bash
# Option 1: Explicit compose file merge
docker compose -f docker-compose.yml \
  -f deployment/tailscale/docker-compose.tailscale.yml up -d

# Option 2: Set COMPOSE_FILE in .env (auto-loaded)
echo "COMPOSE_FILE=docker-compose.yml:deployment/tailscale/docker-compose.tailscale.yml" >> .env
docker compose up -d
```

---

## Setup Guide

### 1. VPS (Hetzner)

```bash
# Run the setup script
bash deployment/tailscale/setup_vps.sh

# Or manually:
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --hostname=shamrock-vps --advertise-routes=172.18.0.0/16 --accept-routes --ssh
```

### 2. Office iMac

```bash
# Run the setup script via Tailscale SSH (if already connected)
ssh shamrockbailbonds@shamrocksimac "bash -s" < deployment/tailscale/setup_imac.sh

# Key steps:
# 1. Enable exit node: tailscale set --advertise-exit-node
# 2. Install microsocks for SOCKS5 proxy
# 3. Approve exit node in Tailscale admin console
```

### 3. Laptop

```bash
# Install Tailscale (macOS)
brew install tailscale
# OR download from https://tailscale.com/download/mac

# Authenticate
tailscale up --hostname=shamrock-laptop --accept-routes
```

### 4. Tailscale Admin Console

After all nodes are connected:

1. Go to [login.tailscale.com/admin/machines](https://login.tailscale.com/admin/machines)
2. **Approve subnet routes** on `shamrock-vps` (172.18.0.0/16)
3. **Approve exit node** on `shamrocksimac`
4. **Apply ACL policy** from `deployment/tailscale/acl-policy.json`

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tailscale/status` | GET | Full tailnet health summary |
| `/api/tailscale/check` | POST | Trigger immediate peer health check |
| `/api/tailscale/peers` | GET | List peers with latency and service status |

---

## Code Integration Points

| File | Integration |
|------|-------------|
| `config/tailscale.py` | Central Tailscale config + service discovery |
| `dashboard/services/tailscale_health.py` | Health monitor + failover logic |
| `dashboard/routers/tailscale_status.py` | Dashboard API endpoints |
| `dashboard/routers/bb_health_monitor.py` | BB health check uses Tailscale-first routing |
| `deployment/tailscale/docker-compose.tailscale.yml` | Docker sidecar + SOCKS proxy |
| `deployment/tailscale/setup_vps.sh` | VPS installation script |
| `deployment/tailscale/setup_imac.sh` | iMac configuration script |
| `deployment/tailscale/acl-policy.json` | Tailnet ACL policy |
| `.env.example` | Environment variable documentation |

---

## Monitoring & Troubleshooting

### Check Tailscale status
```bash
# On any node
tailscale status

# Ping a peer
tailscale ping shamrocksimac

# Check exit node routing
tailscale status --json | jq '.ExitNodeStatus'
```

### Dashboard health check
```bash
# Via API
curl -s http://localhost:8088/api/tailscale/status | python3 -m json.tool

# Force check
curl -s -X POST http://localhost:8088/api/tailscale/check | python3 -m json.tool
```

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Tailscale peer unreachable | Node offline or tailscaled not running | Check `tailscale status` on both ends |
| Exit node not routing | Not approved in admin | Approve at login.tailscale.com/admin/machines |
| MagicDNS not resolving | DNS not accepted | `tailscale set --accept-dns=true` |
| Docker containers can't reach tailnet | Sidecar not running | `docker compose up -d tailscale` |
| BB health shows ngrok instead of Tailscale | iMac port 1234 not reachable via TS | Check iMac firewall, verify BB is running |

---

## Security Notes

- **WireGuard encryption**: All traffic between nodes is encrypted end-to-end
- **No public ports needed**: BlueBubbles, SOCKS, SSH all stay private
- **ACLs**: Fine-grained control over which devices can reach which services
- **Tailscale SSH**: Replaces key-based SSH with identity-based access
- **Audit logs**: All connections logged in Tailscale admin console
- **Key rotation**: Auth keys are ephemeral; device keys rotate automatically

---

## Migration Path

The system is designed for **gradual migration** — Tailscale and legacy paths coexist:

1. **Phase 1** (current): Tailscale installed, failover enabled, ngrok still active
2. **Phase 2**: Verify Tailscale stability over 1 week, monitor failover events
3. **Phase 3**: Disable ngrok LaunchAgent on iMac (keep installed as emergency backup)
4. **Phase 4**: Remove frps from docker-compose (keep config for rollback)
5. **Phase 5**: Remove SSH reverse tunnel LaunchAgent

**Never remove the fallback code** — Tailscale is the primary path, but the failover
to ngrok/frp ensures zero downtime if Tailscale has an outage.
