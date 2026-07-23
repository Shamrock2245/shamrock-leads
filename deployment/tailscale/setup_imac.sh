#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ShamrockLeads — Tailscale iMac Configuration
# Run this ON THE OFFICE iMAC to configure Tailscale as:
#   1. Exit node (residential IP for scraper traffic)
#   2. BlueBubbles direct access (no more ngrok/frp)
#   3. SOCKS5 proxy server (replaces SSH -R tunnel)
#
# Prerequisites:
#   - Tailscale already installed (brew install tailscale OR Mac App Store)
#   - Tailscale already authenticated (should be — per bluebubbles-tunnel.md)
#   - Device name: shamrocksimac (already set)
#   - Tailscale IP: 100.102.10.86 (already assigned)
#
# Usage:
#   ssh shamrockbailbonds@shamrocksimac "bash -s" < deployment/tailscale/setup_imac.sh
#   OR run locally on the iMac
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

echo "═══════════════════════════════════════════════════"
echo "  🍀 ShamrockLeads — Tailscale iMac Setup"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Step 1: Verify Tailscale is running ──
if ! command -v tailscale &>/dev/null; then
    echo "❌ Tailscale not found. Install via:"
    echo "   brew install tailscale"
    echo "   OR download from https://tailscale.com/download/mac"
    exit 1
fi

echo "✅ Tailscale version: $(tailscale version 2>/dev/null || echo 'GUI app')"

# ── Step 2: Enable exit node (advertise this device as residential exit) ──
echo ""
echo "🌐 Enabling exit node (residential IP egress for VPS scrapers)..."
echo "   This allows the VPS to route traffic through your office IP."
echo ""

# On macOS with the GUI app, exit node must be enabled in System Settings
# For CLI-managed Tailscale:
if tailscale status &>/dev/null; then
    # Check if already advertising as exit node
    if tailscale status --json 2>/dev/null | grep -q '"ExitNode"'; then
        echo "   ✅ Already configured as exit node"
    else
        echo "   Enabling exit node advertisement..."
        tailscale set --advertise-exit-node 2>/dev/null || \
            echo "   ⚠️  Use Tailscale GUI: Preferences → 'Run as exit node' → Enable"
    fi
else
    echo "   ⚠️  Tailscale not connected. Open the Tailscale app and sign in."
fi

# ── Step 3: Verify BlueBubbles is accessible on Tailscale IP ──
echo ""
echo "🔵 Verifying BlueBubbles accessibility on Tailscale..."
BB_PORT=1234
BB_LOCAL="http://localhost:${BB_PORT}/api/v1/ping"

if curl -sf "$BB_LOCAL" > /dev/null 2>&1; then
    echo "   ✅ BlueBubbles running on localhost:${BB_PORT}"
    echo "   ✅ Accessible via Tailscale at: http://100.102.10.86:${BB_PORT}"
    echo "      (or http://shamrocksimac:${BB_PORT} via MagicDNS)"
else
    echo "   ⚠️  BlueBubbles not responding on localhost:${BB_PORT}"
    echo "   Make sure BlueBubbles.app is running."
fi

# ── Step 4: Install and configure SOCKS5 proxy (replaces SSH tunnel) ──
echo ""
echo "🔌 Setting up SOCKS5 proxy (replaces SSH -R 1080 tunnel)..."

# Check if microsocks or dante is available
SOCKS_PORT=1080
if command -v microsocks &>/dev/null; then
    echo "   ✅ microsocks already installed"
elif command -v brew &>/dev/null; then
    echo "   📦 Installing microsocks via Homebrew..."
    brew install microsocks 2>/dev/null || echo "   ⚠️  Install manually: brew install microsocks"
fi

# Create LaunchAgent for SOCKS5 proxy
PLIST_PATH="$HOME/Library/LaunchAgents/com.shamrock.socks-proxy.plist"
if [ ! -f "$PLIST_PATH" ]; then
    echo "   📝 Creating LaunchAgent for SOCKS5 proxy..."
    cat > "$PLIST_PATH" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.shamrock.socks-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/microsocks</string>
        <string>-i</string>
        <string>0.0.0.0</string>
        <string>-p</string>
        <string>1080</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/socks-proxy.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/socks-proxy-err.log</string>
</dict>
</plist>
PLIST
    launchctl load "$PLIST_PATH" 2>/dev/null || true
    echo "   ✅ SOCKS5 proxy LaunchAgent created and loaded"
else
    echo "   ✅ SOCKS5 proxy LaunchAgent already exists"
fi

# ── Step 5: macOS firewall (allow Tailscale + SOCKS) ──
echo ""
echo "🔒 Firewall notes:"
echo "   macOS firewall should allow incoming connections for:"
echo "     - Tailscale (automatic via app)"
echo "     - BlueBubbles (port 1234) — only from Tailscale IPs (100.x.x.x)"
echo "     - SOCKS5 proxy (port 1080) — only from Tailscale IPs"
echo ""
echo "   These ports are NOT exposed to the public internet —"
echo "   they're only reachable via the Tailscale mesh (WireGuard encrypted)."

# ── Step 6: Verify connectivity ──
echo ""
echo "📊 Tailscale Status:"
tailscale status 2>/dev/null || echo "   (run 'tailscale status' manually)"
echo ""
echo "🌐 Tailscale IP: $(tailscale ip -4 2>/dev/null || echo '100.102.10.86')"
echo ""

# ── Summary ──
echo "═══════════════════════════════════════════════════"
echo "  ✅ iMac Tailscale configuration complete!"
echo ""
echo "  Services now accessible via Tailscale:"
echo "    BlueBubbles: http://shamrocksimac:1234"
echo "    SOCKS5:      socks5://shamrocksimac:1080"
echo "    SSH:         ssh shamrockbailbonds@shamrocksimac"
echo ""
echo "  ⚠️  IMPORTANT: Approve exit node in Tailscale admin:"
echo "    https://login.tailscale.com/admin/machines"
echo "    → Find 'shamrocksimac' → Approve as exit node"
echo "═══════════════════════════════════════════════════"
