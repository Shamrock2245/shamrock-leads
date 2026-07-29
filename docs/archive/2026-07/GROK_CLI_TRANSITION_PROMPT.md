# Grok CLI Transition Prompt: ShamrockLeads Autonomous Proxy Engine (APE)

**Status (2026-07-16):** Warren hub is live on production. First residential node `mac-office` is enrolled and proxying. Lead Explorer + scraper-health multi-state join is on `main`.

**Repo:** `Shamrock2245/shamrock-leads` · **VPS:** `178.156.179.237`  
**Do not use** the outdated IP `5.161.126.32` or bare Warren binary URLs from earlier handoffs.

---

## 1. Pull latest

```bash
cd /path/to/shamrock-leads
git pull origin main
```

---

## 2. Warren Hub (already deployed)

Hub systemd unit on the Hetzner VPS:

| Port | Role |
|------|------|
| **7000** | Nodes dial in (TLS) |
| **8000** | Client proxy (HTTP CONNECT / SOCKS5) |
| **9000** | Admin dashboard (token auth) |

```bash
ssh -i ~/.ssh/id_ed25519 root@178.156.179.237
systemctl status warren
journalctl -u warren -n 40 --no-pager   # join code + enroll token
sudo cat /opt/warren/hub.env              # WARREN_PROXY_PASS, tokens (mode 600)
```

Re-deploy / upgrade:

```bash
# On the VPS, from repo:
bash deployment/warren_hub_deploy.sh v0.4.11
```

---

## 3. macOS residential node enrollment (done: `mac-office`)

**Correct flow** uses the one-paste **join code** from hub logs (not the old `wss://5.x.x.x:8000` form).

```bash
cd /path/to/shamrock-leads/deployment

# Grab join code on VPS:
#   journalctl -u warren -n 20 | grep 'join a device'

./warren_node_enroll.sh \
  --join 'warren1.XXXX' \
  --name mac-office \
  --install-service
```

- Binary installs to `~/.local/bin/warren`
- launchd agent: `~/Library/LaunchAgents/com.warren.node.plist`
- Check: `launchctl print gui/$(id -u)/com.warren.node`
- Hub log should show: `node enrolled node=mac-office-…`

**Additional Mac/PC/RPi nodes** — same script, unique `--name` (e.g. `home-rpi`, `laptop-brendan`).

### Verify residential exit

```bash
# On VPS (password from /opt/warren/hub.env):
source /opt/warren/hub.env
curl -x "http://${WARREN_PROXY_USER}:${WARREN_PROXY_PASS}@127.0.0.1:8000" https://api.ipify.org
# Should return the enrolled device's home/public IP (not the VPS IP)
```

---

## 4. iPhone / mobile exit (Proxy Factory)

Warren **does not** ship a native iOS App Store binary. Use one of these:

### Option A — Android phone (true carrier IP) — S5W2C

1. Install [S5W2C](https://github.com/h4ckm310n/S5W2C) APK  
2. Enable mobile data; join same Wi‑Fi as the scraper **or** expose via tunnel  
3. Start SOCKS5 server (default port `1080`)  
4. Set in `.env`:

```bash
S5W2C_PHONE_IP=<phone LAN IP>
S5W2C_PORT=1080
S5W2C_ENABLED=true
```

### Option B — Android Termux Warren node

```bash
# On Android Termux (see doedja/warren README):
curl -fsSL https://raw.githubusercontent.com/doedja/warren/main/install.sh | sh -s -- --join warren1.XXXX
```

Name example matching older handoff: `--name iphone-mobile-node` is only appropriate if the **device running the node** is actually that phone (prefer names like `android-pixel`, `pixel-hotspot`).

### Option C — iPhone personal hotspot

1. Enable iPhone Personal Hotspot  
2. Connect Mac (or a small always-on box) to that hotspot  
3. Enroll **that Mac** as a Warren node (e.g. `--name iphone-hotspot-mac`)  
4. Traffic exits via the phone’s carrier IP while tethered  

```bash
./warren_node_enroll.sh --join 'warren1.XXXX' --name iphone-hotspot-mac --install-service
```

---

## 5. Environment (scraper + dashboard containers)

```bash
# Production / local .env (never commit secrets)
WARREN_HUB_URL=178.156.179.237:8000
WARREN_PROXY_USER=warren
WARREN_PASSWORD=          # = WARREN_PROXY_PASS from /opt/warren/hub.env
WARREN_ENABLED=true       # only after ≥1 node is enrolled

S5W2C_PHONE_IP=
S5W2C_PORT=1080
S5W2C_ENABLED=false

STORMSIA_ENABLED=true
STORMSIA_CACHE_TTL=1800
PROXY_PREFER_RESIDENTIAL=true
```

Restart scrapers after changing env:

```bash
ssh root@178.156.179.237 'cd /opt/shamrock-leads && docker compose up -d shamrock-leads'
```

---

## 6. Local APE tests

```bash
cd /path/to/shamrock-leads
PYTHONPATH=$(pwd) python3 tests/test_ape_integration.py
```

---

## 7. Scraper integration

- Helpers on `BaseScraper`: `get_proxy()`, `get_sticky_proxy()`, `record_proxy_success()`, `record_proxy_failure()`
- Example: `scrapers/counties/tennessee_tncis_v2_ape.py`
- Core: `scrapers/proxy_engine.py`, `scrapers/stealth_utils.py`

---

## 8. Key file map

| File | Role |
|------|------|
| `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md` | Architecture |
| `docs/APE_INTEGRATION_GUIDE.md` | Setup + usage |
| `docs/GROK_CLI_TRANSITION_PROMPT.md` | This handoff |
| `deployment/warren_hub_deploy.sh` | Hub on VPS |
| `deployment/warren_node_enroll.sh` | Device enrollment |
| `scrapers/proxy_engine.py` | APE runtime |
| `tests/test_ape_integration.py` | Tests |

---

## Corrections vs older agent notes

| Wrong (old handoff) | Correct |
|---------------------|---------|
| Hub IP `5.161.126.32` | **`178.156.179.237`** |
| Node hub URL `wss://…:8000` | Nodes use **`:7000`**; prefer **`--join warren1.…`** |
| Bare binary `warren-x86_64-unknown-linux-gnu` | Tarball `warren-v0.4.11-linux-x86_64.tar.gz` |
| SSH key `.shamrock_deploy_key` | **`~/.ssh/id_ed25519`** for this VPS |
| Enable Warren with zero nodes | Enroll ≥1 node first; then `WARREN_ENABLED=true` |

---

**Last updated:** 2026-07-16 · Hub live · Node `mac-office` enrolled · APE + Lead Explorer on `main`
