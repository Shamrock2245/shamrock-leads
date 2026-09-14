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
#   ssh root@178.156.179.237 "bash -s" < deployment/tailscale/setup_vps.sh
#   OR
#   scp deployment/tailscale/setup_vps.sh root@178.156.179.237:/tmp/ && \
#     ssh root@178.156.179.237 "bash /tmp/setup_vps.sh"
#
# Prerequisites:
#   - TAILSCALE_AUTHKEY set in environment or passed as $1
#   - Root access on Hetzner CCX33 VPS (178.156.179.237)
#   - Hetzner Cloud Console Firewall rule: Inbound UDP 41641 (0.0.0.0/0, ::/0)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

AUTHKEY="${TAILSCALE_AUTHKEY:-${1:-}}"
HOSTNAME="shamrock-vps"
TAILNET="shamrockbailbonds.biz"

echo "═══════════════════════════════════════════════════"
echo "  🍀 ShamrockLeads — Tailscale Hetzner VPS Setup"
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

# ── Step 4: Enable IP forwarding & optimize network buffers ──
echo ""
echo "🌐 Enabling IP forwarding & tuning buffers for Tailscale..."
cat > /etc/sysctl.d/99-tailscale.conf << 'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
EOF
sysctl -p /etc/sysctl.d/99-tailscale.conf

# ── Step 5: Enable UDP GRO forwarding offload (Tailscale Linux Performance) ──
if command -v ethtool &>/dev/null; then
    DEFAULT_IFACE=$(ip route show default | awk '{print $5}' | head -n1)
    if [ -n "$DEFAULT_IFACE" ]; then
        echo "⚡ Enabling UDP GRO forwarding offload on $DEFAULT_IFACE..."
        ethtool -K "$DEFAULT_IFACE" rx-udp-gro-forwarding on rx-gro-list off 2>/dev/null || true
    fi
fi

# ── Step 6: Host firewall rules ──
echo ""
echo "🔒 Configuring host firewall for Tailscale..."
if command -v ufw &>/dev/null; then
    ufw allow in on tailscale0 comment "Tailscale mesh traffic"
    ufw allow 41641/udp comment "Tailscale WireGuard Easy NAT"
    echo "   ✅ UFW rules configured"
elif command -v iptables &>/dev/null; then
    iptables -I INPUT -i tailscale0 -j ACCEPT 2>/dev/null || true
    iptables -I INPUT -p udp --dport 41641 -j ACCEPT 2>/dev/null || true
    echo "   ✅ iptables rules configured"
fi

# ── Step 7: Verify ──
echo ""
echo "📊 Tailscale Status:"
tailscale status
echo ""
echo "🌐 Tailscale IP:"
tailscale ip -4
echo ""
echo "📡 NAT / DERP Check:"
tailscale netcheck || true
echo ""

# ── Step 8: Hetzner Cloud Firewall Checklist ──
echo "═══════════════════════════════════════════════════"
echo "  🛡️  HETZNER CLOUD CONSOLE FIREWALL CHECKLIST"
echo "═══════════════════════════════════════════════════"
echo "In Hetzner Cloud Console (console.hetzner.cloud) → Firewalls:"
echo "  1. Inbound rule: Allow UDP port 41641 from 0.0.0.0/0 and ::/0"
echo "     → CRITICAL: Without this, connections to iMac drop to DERP relays!"
echo "  2. Outbound rule: Allow UDP port 3478 to 0.0.0.0/0 and ::/0 (STUN)"
echo "  3. SSH Lockdown: Once 'tailscale ssh root@$HOSTNAME' works,"
echo "     remove public port 22 or restrict to authorized IP to eliminate bot brute force."
echo "  4. Approve subnet routes in Tailscale admin console:"
echo "     https://login.tailscale.com/admin/machines"
echo "     → Find '$HOSTNAME' → Edit route settings → Approve: 172.18.0.0/16"
echo "═══════════════════════════════════════════════════"
echo "  ✅ Tailscale VPS setup complete!"
echo "═══════════════════════════════════════════════════"
