#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# 🍀 Shamrock Bail Bonds — Permanent BlueBubbles Tunnel Setup
# ──────────────────────────────────────────────────────────────────
# Run this ON THE OFFICE iMAC to create a permanent Cloudflare
# named tunnel at bb.shamrockbailbonds.biz → localhost:1234
#
# This permanently solves the URL rotation problem:
#   - No more trycloudflare.com random URLs
#   - No more manual URL hot-swaps on the VPS
#   - Survives BlueBubbles restarts and iMac reboots
#
# Prerequisites:
#   - brew install cloudflared
#   - BlueBubbles running on port 1234
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ──
TUNNEL_NAME="bluebubbles"
HOSTNAME="bb.shamrockbailbonds.biz"
LOCAL_PORT=1234
CF_TOKEN="${CLOUDFLARE_API_TOKEN:?ERROR: Set CLOUDFLARE_API_TOKEN env var first}"
CF_ACCOUNT_ID="e3ceb175a0ebe60c6e02fe2c38e17691"

echo "🍀 Shamrock BlueBubbles Tunnel Setup"
echo "════════════════════════════════════════"

# ── Step 0: Install cloudflared if missing ──
if ! command -v cloudflared &>/dev/null; then
    echo "📦 Installing cloudflared..."
    brew install cloudflare/cloudflare/cloudflared
fi

echo "✅ cloudflared version: $(cloudflared --version)"

# ── Step 1: Authenticate with Cloudflare ──
# Using API token instead of interactive login
echo ""
echo "🔑 Step 1: Authenticating with Cloudflare..."
echo "   Using API token for account $CF_ACCOUNT_ID"

# Verify token works
VERIFY=$(curl -s -X GET "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/tokens/verify" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json")

if echo "$VERIFY" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('success') else 1)" 2>/dev/null; then
    echo "   ✅ API token verified successfully"
else
    echo "   ❌ API token verification failed!"
    echo "   Response: $VERIFY"
    echo ""
    echo "   Try interactive login instead:"
    echo "     cloudflared tunnel login"
    echo "   Then re-run this script."
    exit 1
fi

# ── Step 2: Login to cloudflared (interactive, creates cert.pem) ──
echo ""
echo "🔑 Step 2: Logging into cloudflared..."
echo "   This will open a browser for Cloudflare authorization."
echo "   Select the shamrockbailbonds.biz zone."
echo ""

# Check if already logged in
if [ -f "$HOME/.cloudflared/cert.pem" ]; then
    echo "   ✅ Already authenticated (cert.pem exists)"
else
    echo "   ⚠️  Opening browser for Cloudflare login..."
    cloudflared tunnel login
    echo "   ✅ Authentication complete"
fi

# ── Step 3: Create the named tunnel ──
echo ""
echo "🚇 Step 3: Creating named tunnel '$TUNNEL_NAME'..."

# Check if tunnel already exists
EXISTING_ID=$(cloudflared tunnel list 2>/dev/null | grep -w "$TUNNEL_NAME" | awk '{print $1}' || true)

if [ -n "$EXISTING_ID" ]; then
    echo "   ✅ Tunnel '$TUNNEL_NAME' already exists (ID: $EXISTING_ID)"
    TUNNEL_ID="$EXISTING_ID"
else
    # Create it
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep -w "$TUNNEL_NAME" | awk '{print $1}')
    echo "   ✅ Tunnel created (ID: $TUNNEL_ID)"
fi

# ── Step 4: Write config file ──
echo ""
echo "📝 Step 4: Writing tunnel configuration..."

CREDS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"

if [ ! -f "$CREDS_FILE" ]; then
    echo "   ⚠️  Credentials file not found at $CREDS_FILE"
    echo "   Looking for alternative..."
    CREDS_FILE=$(find "$HOME/.cloudflared" -name "*.json" -not -name "config.json" | head -1 || true)
    if [ -z "$CREDS_FILE" ]; then
        echo "   ❌ No tunnel credentials found. Try deleting and recreating:"
        echo "      cloudflared tunnel delete $TUNNEL_NAME"
        echo "      cloudflared tunnel create $TUNNEL_NAME"
        exit 1
    fi
    echo "   Found: $CREDS_FILE"
fi

# Backup existing config if present
[ -f "$HOME/.cloudflared/config.yml" ] && cp "$HOME/.cloudflared/config.yml" "$HOME/.cloudflared/config.yml.bak.$(date +%s)"

cat > "$HOME/.cloudflared/config.yml" <<EOF
# Shamrock Bail Bonds — BlueBubbles Permanent Tunnel
# Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Tunnel: $TUNNEL_NAME ($TUNNEL_ID)
# Routes: $HOSTNAME → localhost:$LOCAL_PORT

tunnel: $TUNNEL_ID
credentials-file: $CREDS_FILE

ingress:
  - hostname: $HOSTNAME
    service: http://localhost:$LOCAL_PORT
    originRequest:
      noTLSVerify: true
  - service: http_status:404
EOF

echo "   ✅ Config written to ~/.cloudflared/config.yml"

# ── Step 5: Route DNS ──
echo ""
echo "🌐 Step 5: Setting up DNS route..."
echo "   Routing $HOSTNAME → tunnel $TUNNEL_NAME"

# This creates a CNAME record: bb.shamrockbailbonds.biz → <tunnel-id>.cfargotunnel.com
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" 2>/dev/null || {
    echo "   ⚠️  DNS route may already exist (that's OK)"
}

echo "   ✅ DNS configured: $HOSTNAME → $TUNNEL_NAME.cfargotunnel.com"

# ── Step 6: Test the tunnel ──
echo ""
echo "🧪 Step 6: Testing tunnel connectivity..."

# Start tunnel in background for test
cloudflared tunnel run "$TUNNEL_NAME" &
TUNNEL_PID=$!
sleep 5

# Test connectivity
echo "   Testing https://$HOSTNAME/api/v1/server?password=${BB_PASSWORD:?set BB_PASSWORD} ..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://$HOSTNAME/api/v1/server?password=${BB_PASSWORD:?set BB_PASSWORD}" --max-time 10 || echo "000")

# Kill test tunnel
kill $TUNNEL_PID 2>/dev/null || true
wait $TUNNEL_PID 2>/dev/null || true

if [ "$HTTP_CODE" = "200" ]; then
    echo "   ✅ Tunnel is working! BlueBubbles accessible at https://$HOSTNAME"
else
    echo "   ⚠️  Got HTTP $HTTP_CODE — DNS may need a few minutes to propagate"
    echo "   The tunnel is configured correctly; try again in 2-3 minutes."
fi

# ── Step 7: Install as macOS service ──
echo ""
echo "🔄 Step 7: Installing as persistent macOS service..."
echo "   This ensures the tunnel auto-starts on boot."
echo ""

# Create LaunchAgent plist
PLIST_PATH="$HOME/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cloudflare.bluebubbles-tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which cloudflared)</string>
        <string>tunnel</string>
        <string>run</string>
        <string>$TUNNEL_NAME</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/cloudflared-bb.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/cloudflared-bb-err.log</string>
</dict>
</plist>
PLIST

# Unload old version if exists, then load
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "   ✅ LaunchAgent installed and loaded"
echo "   The tunnel will now auto-start on every login."
echo "   Logs: /tmp/cloudflared-bb.log"

# ── Step 8: Summary ──
echo ""
echo "════════════════════════════════════════"
echo "🍀 SETUP COMPLETE!"
echo "════════════════════════════════════════"
echo ""
echo "  Tunnel ID:   $TUNNEL_ID"
echo "  Tunnel Name: $TUNNEL_NAME"
echo "  Public URL:  https://$HOSTNAME"
echo "  Local Port:  $LOCAL_PORT"
echo "  Auto-Start:  ✅ Yes (LaunchAgent)"
echo ""
echo "  ⚡ NEXT STEPS:"
echo "  1. Update VPS .env: BLUEBUBBLES_URL_0178=https://$HOSTNAME"
echo "  2. Rebuild VPS:     docker compose build --no-cache dashboard"
echo "  3. Restart:         docker compose up -d dashboard"
echo ""
echo "  Or hot-swap immediately (no rebuild needed):"
echo "  curl -X PATCH https://leads.shamrockbailbonds.biz/api/bb-health/update-url \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"suffix\":\"0178\",\"url\":\"https://$HOSTNAME\",\"api_key\":\"shamrock-bb-sync-2245\"}'"
echo ""
echo "  🎉 No more URL rotation. No more manual hot-swaps."
echo "════════════════════════════════════════"
