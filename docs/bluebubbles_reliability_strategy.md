# BlueBubbles Reliability & Tunnel Architecture Strategy

## The Problem
BlueBubbles (BB) on the office M1 iMac experiences periodic crashes and disconnects. Historically this used **ngrok** or **Cloudflare quick/named tunnels** (`*.trycloudflare.com` / `bb.shamrockbailbonds.biz`). Those tunnels fail when DNS or the tunnel agent dies — the VPS then logs `BB [0178] DNS FAILED`.

## Current standard: **frp** (self-hosted)

We **replace Cloudflare Tunnel and ngrok** with [frp](https://github.com/fatedier/frp):

| Component | Where | Purpose |
|-----------|--------|---------|
| **frps** | Hetzner Docker (`profile: tunnel`) | Accepts outbound frpc; exposes BB on `:12434` |
| **frpc** | Office iMac LaunchDaemon | Outbound-only tunnel from BB `localhost:1234` |
| **Dashboard** | `BLUEBUBBLES_URL_*` | Points at VPS frp endpoint (or nginx TLS front) |

Full runbook: **`docs/FRP_TUNNEL.md`**.

### Why not Cloudflare / ngrok anymore
- Quick tunnels rotate hostnames and break DNS.
- Named tunnels still depend on Cloudflare control plane + `cloudflared` agent health.
- frp keeps the control plane on **our** VPS; iMac only needs outbound TCP 7000.

### Optional later: Pangolin
[fosrl/pangolin](https://github.com/fosrl/pangolin) is a WireGuard zero-trust alternative if we need SSO/dashboard for many services. Start with frp for BB.

---

## Host reliability (iMac)

1. **BlueBubbles Server ≥ v1.9.9** (M1 crash fixes).
2. **Watchdog** LaunchAgent: ping `http://localhost:1234/api/v1/ping` every 5m; restart BB if down.
3. **frpc LaunchDaemon** (`com.shamrock.frpc`) with `KeepAlive` so tunnel survives reboot/login.

---

## VPS monitoring

- Dashboard `bb_health_monitor` + iMessage status API.
- On tunnel failure, queue outbound messages rather than drop (existing automation path).

---

## Cutover checklist

1. `docker compose --profile tunnel up -d frps` on VPS  
2. Install frpc on iMac with matching token  
3. Set `BLUEBUBBLES_URL_0178=http://178.156.179.237:12434` (or HTTPS via nginx)  
4. Restart `shamrock-dashboard`  
5. Stop `cloudflared` / ngrok agents  
6. Confirm `/api/imessage/status` shows connected  

---

**References**
- [frp](https://github.com/fatedier/frp)
- [Pangolin](https://github.com/fosrl/pangolin)
- [BlueBubbles Server releases](https://github.com/BlueBubblesApp/bluebubbles-server/releases)
