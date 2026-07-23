#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ShamrockLeads — Tailscale VPS Setup
# Run this ON THE HETZNER VPS to install and configure Tailscale
#
# This script:
#   1. Installs Tailscale on the host (for direct SSH access)
#   2. Authenticates with the tailnet
#   3. Enables subnet routing (Docker bridge → tailnet)
#   4. Enables the Docker sidecar for container-level tailnet access
#   5. Configures firewall rules for Tailscale
#
# Usage:
#   ssh root@5.161.126.32 "bash -s" < deployment/tailscale/setup_vps.sh
#   OR
#   scp deployment/tailscale/setup_vps.sh root@5.161.126.32:/tmp/ && \
#     ssh root@5.161.126.32 "bash /tmp/setup_vps.sh"
#
# Prerequisites:
#   - TAILSCALE_AUTHKEY set in environment or passed as $1
#   - Root access on the VPS
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

AUTHKEY="${TAILSCALE_AUTHKEY:-${1:-}}"
HOSTNAME="shamrock-vps"
TAILNET="shamrockbailbonds.biz"

echo "═══════════════════════════════════════════════════"
echo "  🍀 ShamrockLeads — Tailscale VPS Setup"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Step 1: Install Tailscale ──
if command -v tailscale &>/dev/null; then
    echo "✅ Tailscale already installed: $(tailscale version)"
else
    echo "📦 Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "✅ Tailscale installed: $(tailscale version)"
fi

# ── Step 2: Enable and start tailscaled ──
echo ""
echo "🔧 Enabling tailscaled service..."
systemctl enable --now tailscaled
sleep 2

# ── Step 3: Authenticate and configure ──
echo ""
echo "🔑 Authenticating with tailnet..."

TS_ARGS=(
    --hostname="$HOSTNAME"
    --accept-routes
    --accept-dns=true
    --advertise-routes=172.18.0.0/16
    --ssh
)

if [ -n "$AUTHKEY" ]; then
    TS_ARGS+=(--authkey="$AUTHKEY")
    tailscale up "${TS_ARGS[@]}"
    echo "✅ Authenticated via auth key"
else
    echo "⚠️  No TAILSCALE_AUTHKEY provided."
    echo "   Running interactive auth (will print a URL to visit):"
    tailscale up "${TS_ARGS[@]}"
fi

# ── Step 4: Enable IP forwarding (for subnet router) ──
echo ""
echo "🌐 Enabling IP forwarding for subnet routing..."
cat > /etc/sysctl.d/99-tailscale.conf << 'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-tailscale.conf

# ── Step 5: Firewall rules ──
echo ""
echo "🔒 Configuring firewall for Tailscale..."
# Allow Tailscale interface traffic
if command -v ufw &>/dev/null; then
    ufw allow in on tailscale0 comment "Tailscale mesh traffic"
    ufw allow 41641/udp comment "Tailscale WireGuard"
    echo "   ✅ UFW rules added"
elif command -v iptables &>/dev/null; then
    iptables -I INPUT -i tailscale0 -j ACCEPT 2>/dev/null || true
    iptables -I INPUT -p udp --dport 41641 -j ACCEPT 2>/dev/null || true
    echo "   ✅ iptables rules added"
fi

# ── Step 6: Verify ──
echo ""
echo "📊 Tailscale Status:"
tailscale status
echo ""
echo "🌐 Tailscale IP:"
tailscale ip -4
echo ""

# ── Step 7: Docker sidecar setup ──
echo ""
echo "🐳 Docker Tailscale sidecar notes:"
echo "   To enable container-level tailnet access, add to .env:"
echo "     TAILSCALE_AUTHKEY=tskey-auth-..."
echo "     COMPOSE_FILE=docker-compose.yml:deployment/tailscale/docker-compose.tailscale.yml"
echo ""
echo "   Then run:"
echo "     docker compose up -d tailscale"
echo ""

# ── Step 8: Approve subnet routes ──
echo "⚠️  IMPORTANT: Approve subnet routes in Tailscale admin console:"
echo "   https://login.tailscale.com/admin/machines"
echo "   → Find 'shamrock-vps' → Approve route: 172.18.0.0/16"
echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ Tailscale VPS setup complete!"
echo "═══════════════════════════════════════════════════"
