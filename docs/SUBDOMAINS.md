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
| `paperwork.shamrockbailbonds.biz` | Indemnitor portal | VPS nginx | `:5310` | `nginx/paperwork…conf` |
| `social.shamrockbailbonds.biz` | Postiz + MCP | VPS nginx | `:5200` | `nginx/social…conf` |
| `edit.shamrockbailbonds.biz` | **OpenCut** video editor | VPS nginx → Tailscale laptop | `100.119.187.33:3000` | `nginx/edit…conf` |
| `bb.shamrockbailbonds.biz` | BlueBubbles | Cloudflare tunnel | iMac `:1234` | optional `nginx/bb…conf` |
| `imac.shamrockbailbonds.biz` | iMac SSH | Cloudflare tunnel | iMac `:22` | — |
| `trape.shamrockbailbonds.biz` | Trape OSINT (on-demand) | VPS nginx | `:8099` | `nginx/trape…conf` |

## New host: `edit`

OpenCut is **not** Postiz. `social.*` stays on Postiz `:5200`. The editor is:

```
Internet → edit.shamrockbailbonds.biz → VPS nginx → Tailscale 100.119.187.33:3000
                                              (brendans-macbook-pro-4 ONLY)
```

1. **Wix DNS:** `A` `edit` → `178.156.179.237` (TTL 300)  
2. Laptop: PM2 `opencut-web` + `opencut-controller` (listen `0.0.0.0:3000`)  
3. VPS: `bash scripts/setup_nginx_vhosts.sh --certbot edit`  
4. Verify: `python scripts/check_subdomains.py --live`

Never point `edit` at `shamrocksimac` / the office iMac.

## Adding a subdomain

1. Add a `Subdomain(...)` row in `config/subdomains.py`.  
2. If it terminates on the VPS, add `nginx/<host>.conf`.  
3. Create the Wix DNS record.  
4. Run `scripts/setup_nginx_vhosts.sh --certbot <label>`.  
5. Extend `REQUIRED_HOSTS` in `tests/test_subdomains.py`.
