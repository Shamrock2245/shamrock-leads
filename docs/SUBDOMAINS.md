# Shamrock public hostnames

> **Source of truth:** `config/subdomains.py`  
> **Nginx vhosts:** `nginx/<host>.conf`  
> **Check:** `python scripts/check_subdomains.py` · live: `--live`  
> **Install on VPS:** `bash scripts/setup_nginx_vhosts.sh`

DNS for `shamrockbailbonds.biz` is **Wix**. Do not mint Cloudflare nameservers for the zone.

| Host | Role | Origin | Upstream | Nginx |
|------|------|--------|----------|-------|
| `shamrockbailbonds.biz` | Brand / Wix portal | Wix | Wix | — |
| `www.shamrockbailbonds.biz` | Brand www | Wix CDN | Wix | — |
| `leads.shamrockbailbonds.biz` | Bond Auto-CRM | VPS nginx | `:8088` | `nginx/leads…conf` |
| `school.shamrockbailbonds.biz` | Bail School LMS | Netlify | `shamrock-bail-school` | — |
| `sign.shamrockbailbonds.biz` | DocuSeal | VPS nginx | `:5300` | `nginx/sign…conf` |
| `paperwork.shamrockbailbonds.biz` | Indemnitor / defendant PIN portal | VPS nginx | dashboard `:8088` (`/api/portal/portal-ui`) | `nginx/paperwork…conf` |
| `social.shamrockbailbonds.biz` | Postiz + MCP | VPS nginx | `:5200` | `nginx/social…conf` |
| `edit.shamrockbailbonds.biz` | **OpenCut** video editor | VPS nginx → **Docker** | `:5320` | `nginx/edit…conf` |
| `bb.shamrockbailbonds.biz` | BlueBubbles | Cloudflare tunnel | iMac `:1234` | optional `nginx/bb…conf` |
| `imac.shamrockbailbonds.biz` | iMac SSH | Cloudflare tunnel | iMac `:22` | — |
| `trape.shamrockbailbonds.biz` | Trape OSINT (on-demand) | VPS nginx | `:8099` | `nginx/trape…conf` |

## `edit` — OpenCut on the VPS

OpenCut is **not** Postiz and **not** the laptop. `social.*` stays on Postiz `:5200`.

```
Internet → edit.shamrockbailbonds.biz → VPS nginx → 127.0.0.1:5320 → shamrock-opencut
```

The public hostname is **`edit`** (singular), not `edits`.

```bash
# VPS
cd /opt/shamrock-leads
git pull origin main
# set OPENCUT_AUTH_SECRET in .env (long random, 32+ chars)
docker compose --profile edit up -d --build
bash scripts/setup_nginx_vhosts.sh   # source now ships TLS paths; updates origin
python3 scripts/check_subdomains.py --live
```

Wix DNS: `A` `edit` → `178.156.179.237`. Do not proxy this host over Tailscale.

## Adding a subdomain

1. Add a `Subdomain(...)` row in `config/subdomains.py`.  
2. If it terminates on the VPS, add `nginx/<host>.conf`.  
3. Create the Wix DNS record.  
4. Run `scripts/setup_nginx_vhosts.sh --certbot <label>`.  
5. Extend `REQUIRED_HOSTS` in `tests/test_subdomains.py`.
