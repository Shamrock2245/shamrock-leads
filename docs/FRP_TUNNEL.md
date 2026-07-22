# frp Tunnel — Replace Cloudflare Tunnel & ngrok

**Goal:** Office iMac (BlueBubbles) connects **outbound** to Hetzner. No inbound ports on the iMac. No Cloudflare / ngrok dependency.

| Piece | Role | Host |
|-------|------|------|
| **frps** | Server / control plane | Hetzner `178.156.179.237` |
| **frpc** | Client | Office iMac (LaunchDaemon) |
| **BlueBubbles** | iMessage bridge | iMac `127.0.0.1:1234` → VPS `:12434` |

Upstream: [fatedier/frp](https://github.com/fatedier/frp)

---

## 1. VPS (already in compose)

```bash
# On Hetzner /opt/shamrock-leads
# 1) Set a strong token in deployment/frp/frps.toml (auth.token)
# 2) Same token in iMac frpc.toml
docker compose --profile tunnel up -d frps
docker compose ps frps
# Control plane: TCP 7001  (7000 is APE Warren on this VPS)
# BB proxy:      TCP 12434
# Dashboard:     http://178.156.179.237:7500  (firewall-restrict!)
```

**Firewall:** allow inbound TCP `7001` from the office public IP if you want lock-down; allow `12434` only from the VPS itself if you put nginx TLS in front.

### Optional TLS front (recommended)

```nginx
# /etc/nginx/sites-available/bb.shamrockbailbonds.biz
server {
  listen 443 ssl http2;
  server_name bb.shamrockbailbonds.biz;
  # ssl_certificate ...;
  location / {
    proxy_pass http://127.0.0.1:12434;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 3600s;
  }
}
```

Then set:

```bash
BLUEBUBBLES_URL_0178=https://bb.shamrockbailbonds.biz
BLUEBUBBLES_URL=https://bb.shamrockbailbonds.biz
```

Or plain (dev/ops only):

```bash
BLUEBUBBLES_URL_0178=http://178.156.179.237:12434
```

After env change:

```bash
docker compose up -d shamrock-dashboard
```

---

## 2. Office iMac (frpc)

```bash
# On a Mac with the repo (or copy files)
cp deployment/frp/frpc.bluebubbles.toml.example /tmp/frpc.toml
# edit auth.token to match frps
./deployment/frp/install-frpc-macos.sh /tmp/frpc.toml
```

Ensure BlueBubbles is listening on `127.0.0.1:1234`, then:

```bash
curl -sS "http://127.0.0.1:1234/api/v1/ping"
# From VPS:
curl -sS "http://127.0.0.1:12434/api/v1/ping"
```

---

## 3. Cutover from Cloudflare / ngrok

1. Start frps + frpc and verify ping.
2. Update VPS `.env` `BLUEBUBBLES_URL*` to the frp URL.
3. Restart `shamrock-dashboard`.
4. Disable Cloudflare tunnel / `cloudflared` LaunchAgent on the iMac.
5. Remove any ngrok LaunchAgents.
6. Confirm dashboard iMessage status shows connected.

---

## 4. Pangolin (optional later)

[fosrl/pangolin](https://github.com/fosrl/pangolin) is a WireGuard + identity zero-trust stack. Prefer **frp first** for BlueBubbles TCP; evaluate Pangolin if you need full zero-trust SSO for multiple office apps.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| frpc can't login | Token mismatch; VPS port **7001** open; `docker logs shamrock-frps` |
| VPS can't reach BB | frpc running; BB on 1234; `remotePort 12434` allowed in `allowPorts` |
| Dashboard BB DNS FAILED | Still pointing at old `trycloudflare.com` URL — update `.env` |
| Tunnel up, iMessage fail | BB password / server password mismatch; check BB Server UI |
