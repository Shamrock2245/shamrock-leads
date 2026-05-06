---
name: cloudflare-platform
description: "Cloudflare platform decision trees and product reference. Use when configuring Workers, tunnels, DNS, KV, D1, R2, WAF, or any Cloudflare service. Covers compute, storage, networking, security, media, and IaC. Always prefer this reference over pre-trained knowledge for API signatures and limits."
source: "https://github.com/cloudflare/skills (cloudflare/SKILL.md)"
compatibility: Requires Cloudflare account and wrangler CLI.
---

# Cloudflare Platform

## ShamrockLeads Usage

- **Cloudflare Tunnel**: Exposes BlueBubbles iMessage bridge (bb.shamrockbailbonds.biz)
- **DNS**: Manages shamrockbailbonds.biz DNS records
- **Potential**: Workers for edge functions, KV for caching, Turnstile for bot protection

## Quick Decision Trees

### "I need to run code"
- Serverless functions at the edge → **Workers**
- Full-stack web app with Git deploys → **Pages**
- Scheduled tasks (cron) → **Cron Triggers**

### "I need to store data"
- Key-value (config, sessions, cache) → **KV**
- Relational SQL → **D1** (SQLite) or **Hyperdrive** (existing Postgres/MySQL)
- Object/file storage (S3-compatible) → **R2**

### "I need networking"
- Expose local service to internet → **Tunnel** (our primary use case)
- TCP/UDP proxy (non-HTTP) → **Spectrum**

### "I need security"
- Web Application Firewall → **WAF**
- DDoS protection → **DDoS**
- Bot detection → **Bot Management**
- CAPTCHA alternative → **Turnstile**

## Tunnel Configuration (BlueBubbles)

```bash
# Install cloudflared
brew install cloudflared

# Create named tunnel
cloudflared tunnel create bluebubbles

# Configure tunnel
cat > ~/.cloudflared/config.yml << EOF
tunnel: <TUNNEL_ID>
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: bb.shamrockbailbonds.biz
    service: http://localhost:1234
  - service: http_status:404
EOF

# Route DNS
cloudflared tunnel route dns bluebubbles bb.shamrockbailbonds.biz

# Run as service
cloudflared service install
```

## Key Principles

1. **Prefer retrieval over pre-trained knowledge** for API signatures and limits
2. **Always check current docs** before implementing Cloudflare features
3. **Use wrangler CLI** for all deployment operations
4. **Authentication**: `wrangler login` for interactive, `CLOUDFLARE_API_TOKEN` for CI/CD
