# Nginx Reverse Proxy — `leads.shamrockbailbonds.biz`

## Why This Exists

The ShamrockLeads dashboard runs inside Docker on the Hetzner VPS at port 8088.
Without a reverse proxy, all geo-tracking links sent via iMessage look like:

```
http://178.156.179.237:8088/g/abc123xyz
```

This is a red flag for recipients (raw IP, no HTTPS, non-standard port) and
will be blocked by Apple's link preview system. The fix is a branded HTTPS
subdomain that proxies to the same Docker container.

**After setup, all geo links become:**
```
https://leads.shamrockbailbonds.biz/g/abc123xyz
```

---

## Prerequisites

### 1. Add DNS A Record

In your domain registrar (or Wix DNS panel for `shamrockbailbonds.biz`):

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | `leads` | `178.156.179.237` | 300 |

Wait for propagation (usually 5–15 minutes with TTL 300). Verify:
```bash
dig leads.shamrockbailbonds.biz +short
# Should return: 178.156.179.237
```

### 2. Ensure Port 80 and 443 Are Open

On the Hetzner firewall (Hetzner Cloud Console → Firewall):
- Allow **TCP 80** (inbound) — required for Let's Encrypt ACME challenge
- Allow **TCP 443** (inbound) — HTTPS traffic

Port 8088 can remain open for direct VPN/internal access but is no longer
needed for public traffic.

---

## Installation

SSH into the VPS and run the setup script:

```bash
ssh root@178.156.179.237
cd /opt/shamrock-leads
git pull origin main
bash scripts/setup_nginx_proxy.sh
```

The script will:
1. Install `nginx` and `certbot` (if not already installed)
2. Copy `nginx/leads.shamrockbailbonds.biz.conf` → `/etc/nginx/sites-available/`
3. Enable the site
4. Obtain a Let's Encrypt SSL certificate
5. Update `.env` with `DASHBOARD_PUBLIC_URL=https://leads.shamrockbailbonds.biz`
6. Restart the dashboard Docker container

---

## Manual .env Update (if script fails)

Edit `/opt/shamrock-leads/.env` and set:

```bash
# Branded public URL — replaces raw IP in geo-tracking links
DASHBOARD_PUBLIC_URL=https://leads.shamrockbailbonds.biz

# Also update BB webhook registration URL
BB_WEBHOOK_PUBLIC_URL=https://leads.shamrockbailbonds.biz
```

Then restart the dashboard:
```bash
cd /opt/shamrock-leads
docker compose restart dashboard
```

---

## Verification

```bash
# Health check via branded domain
curl -I https://leads.shamrockbailbonds.biz/health

# Geo capture page (should return 200 with HTML)
curl -I https://leads.shamrockbailbonds.biz/g/test-token

# Check SSL certificate
echo | openssl s_client -connect leads.shamrockbailbonds.biz:443 2>/dev/null \
  | openssl x509 -noout -dates
```

---

## Architecture

```
Internet
    │
    ▼  HTTPS :443
leads.shamrockbailbonds.biz
    │
    ▼  Nginx reverse proxy
localhost:8088
    │
    ▼  Docker port mapping
shamrock-dashboard container :5050
    │
    ▼  FastAPI/Uvicorn
/g/{token}  →  geo_capture_page()
/api/webhooks/bluebubbles  →  bb_webhook_receiver
/api/*  →  all dashboard API endpoints
```

---

## SSL Auto-Renewal

Certbot installs a systemd timer that auto-renews certificates before expiry.

```bash
# Check timer status
systemctl status certbot.timer

# Manual renewal test
certbot renew --dry-run
```

---

## Nginx Config Location

```
nginx/leads.shamrockbailbonds.biz.conf   ← source (tracked in git)
/etc/nginx/sites-available/leads.shamrockbailbonds.biz.conf  ← installed
/etc/nginx/sites-enabled/leads.shamrockbailbonds.biz.conf    ← symlink (enabled)
```

If you update the nginx config in the repo, re-run:
```bash
cp /opt/shamrock-leads/nginx/leads.shamrockbailbonds.biz.conf \
   /etc/nginx/sites-available/leads.shamrockbailbonds.biz.conf
nginx -t && systemctl reload nginx
```
