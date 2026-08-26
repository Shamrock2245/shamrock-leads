# BlueBubbles Reliability & Tunnel Architecture Strategy

## The Problem
BlueBubbles (BB) on the office M1 iMac experiences periodic crashes and disconnects. Historically this used **ngrok** or **Cloudflare quick/named tunnels** (`*.trycloudflare.com` / `bb.shamrockbailbonds.biz`). Those tunnels fail when DNS or the tunnel agent dies — the VPS then logs `BB [0178] DNS FAILED`.

## Current standard: **Tailscale first, frp backup** (both OSS)

| Path | Where | Purpose |
|------|--------|---------|
| **Tailscale** | VPS + office iMac on tailnet `shamrockbailbonds.biz` | Primary. Super CRM uses `http://100.102.10.86:1234` |
| **frp** | Hetzner `frps` + iMac `frpc` | Backup if the mesh is down. BB on VPS `:12434` |
| **Warren** | Hetzner hub `:8000` | **Not a tunnel.** Residential egress for scrapers only |
| **ngrok** | Built into BlueBubbles.app | Do not select for CRM. Firebase must not overwrite Tailscale |
| **`bb.shamrockbailbonds.biz`** | Cloudflare named tunnel | Do not use as CRM URL or Wix URL (MagicDNS shadows `*.shamrockbailbonds.biz` on-mesh). Off-mesh Wix uses Super CRM `/api/imessage/wix/send` |

Full runbooks: **`docs/TAILSCALE_INTEGRATION.md`**, **`docs/FRP_TUNNEL.md`**.

### Why not ngrok / Cloudflare as CRM primary
- ngrok is not OSS, adds an interstitial, and Firebase used to clobber the mesh URL.
- Named tunnel `bb.shamrockbailbonds.biz` fights Tailscale MagicDNS because the tailnet domain is the same zone.
- frp keeps a self-hosted backup on **our** VPS; iMac only needs outbound TCP 7001.

### Optional later: Pangolin
[fosrl/pangolin](https://github.com/fosrl/pangolin) is a WireGuard zero-trust alternative if we need SSO/dashboard for many services. Start with frp for BB.

---

## Host reliability (iMac)

1. **BlueBubbles Server ≥ v1.9.9** (M1 crash fixes).
2. **Watchdog** LaunchAgent: ping `http://localhost:1234/api/v1/ping` every 5m; restart BB if down.
3. **frpc LaunchAgent** (`com.shamrock.frpc`) with `KeepAlive` so tunnel survives login (connects to VPS **:7001**; proxies BB **:12434** + SSH **:12222**).

---

## VPS monitoring

- Dashboard `bb_health_monitor` + iMessage status API.
- On tunnel failure, queue outbound messages rather than drop (existing automation path).

---

## Cutover checklist

1. `docker compose --profile tunnel up -d frps` on VPS  
2. Install frpc on iMac with matching token (serverPort **7001**)  
3. Set `BLUEBUBBLES_URL_0178=http://178.156.179.237:12434` (or HTTPS via nginx)  
4. Restart `shamrock-dashboard`  
5. Stop `cloudflared` / ngrok agents  
6. Confirm `/api/imessage/status` shows connected  

---

**References**
- [frp](https://github.com/fatedier/frp)
- [Pangolin](https://github.com/fosrl/pangolin)
- [BlueBubbles Server releases](https://github.com/BlueBubblesApp/bluebubbles-server/releases)
