# Fix: Permanent BlueBubbles Tunnel URL

## The Problem
You're using TryCloudflare — it generates random URLs that change on every restart.

## The Fix: Named Cloudflare Tunnel (free, permanent URL)
This gives you a URL like `bb.shamrockbailbonds.biz` that NEVER changes.

### Run these on the office iMac:

```bash
# 1. Install cloudflared
brew install cloudflared

# 2. Login to Cloudflare
cloudflared tunnel login

# 3. Create a named tunnel
cloudflared tunnel create bb-office

# 4. Route your domain
cloudflared tunnel route dns bb-office bb.shamrockbailbonds.biz

# 5. Create config
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: bb-office
credentials-file: ~/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: bb.shamrockbailbonds.biz
    service: http://localhost:1234
  - service: http_status:404
EOF

# 6. Install as service (survives reboots)
cloudflared service install
```

### Then update .env ONCE (never changes again):
```
BLUEBUBBLES_URL_0178=https://bb.shamrockbailbonds.biz
```

Want me to write the full setup script?
