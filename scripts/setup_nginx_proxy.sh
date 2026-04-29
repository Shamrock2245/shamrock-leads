#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# ShamrockLeads — Nginx Reverse Proxy Setup
# leads.shamrockbailbonds.biz → localhost:8088
# ════════════════════════════════════════════════════════════════════════════════
#
# PREREQUISITES
#   1. DNS A record must be live before running certbot:
#      leads.shamrockbailbonds.biz → 178.156.179.237
#
#   2. Run this script as root on the Hetzner VPS:
#      ssh root@178.156.179.237
#      bash /opt/shamrock-leads/scripts/setup_nginx_proxy.sh
#
# WHAT THIS SCRIPT DOES
#   1. Installs nginx + certbot (if not already installed)
#   2. Copies the nginx vhost config into /etc/nginx/sites-available/
#   3. Enables the site (symlink to sites-enabled)
#   4. Tests nginx config
#   5. Reloads nginx (HTTP-only first, so ACME challenge can work)
#   6. Obtains a Let's Encrypt SSL certificate via certbot --nginx
#   7. Reloads nginx again (now HTTPS)
#   8. Updates .env to set DASHBOARD_PUBLIC_URL and BB_WEBHOOK_PUBLIC_URL
#   9. Restarts the dashboard Docker container
# ════════════════════════════════════════════════════════════════════════════════
set -euo pipefail

DOMAIN="leads.shamrockbailbonds.biz"
EMAIL="admin@shamrockbailbonds.biz"
REPO_PATH="/opt/shamrock-leads"
NGINX_CONF_SRC="${REPO_PATH}/nginx/${DOMAIN}.conf"
NGINX_CONF_DEST="/etc/nginx/sites-available/${DOMAIN}.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}.conf"
ENV_FILE="${REPO_PATH}/.env"

echo "════════════════════════════════════════════════════════"
echo "  ShamrockLeads — Nginx Reverse Proxy Setup"
echo "  Domain: ${DOMAIN}"
echo "════════════════════════════════════════════════════════"

# ── Step 1: Install nginx + certbot ──────────────────────────────────────────
echo ""
echo "📦 Step 1: Installing nginx and certbot..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx
echo "   ✅ nginx and certbot installed"

# ── Step 2: Copy nginx config ─────────────────────────────────────────────────
echo ""
echo "📄 Step 2: Installing nginx vhost config..."
if [ ! -f "${NGINX_CONF_SRC}" ]; then
    echo "   ❌ Config not found at ${NGINX_CONF_SRC}"
    echo "      Make sure you've pulled the latest code: git pull origin main"
    exit 1
fi
cp "${NGINX_CONF_SRC}" "${NGINX_CONF_DEST}"
echo "   ✅ Config copied to ${NGINX_CONF_DEST}"

# ── Step 3: Enable the site ───────────────────────────────────────────────────
echo ""
echo "🔗 Step 3: Enabling site..."
ln -sf "${NGINX_CONF_DEST}" "${NGINX_ENABLED}"
echo "   ✅ Symlink created: ${NGINX_ENABLED}"

# Remove default site if it exists (it conflicts on port 80)
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    rm -f /etc/nginx/sites-enabled/default
    echo "   ✅ Removed default site (was blocking port 80)"
fi

# ── Step 4: Test nginx config ─────────────────────────────────────────────────
echo ""
echo "🧪 Step 4: Testing nginx config..."
nginx -t
echo "   ✅ nginx config OK"

# ── Step 5: Reload nginx (HTTP only for ACME challenge) ───────────────────────
echo ""
echo "🔄 Step 5: Reloading nginx (HTTP mode for ACME challenge)..."
systemctl reload nginx
echo "   ✅ nginx reloaded"

# ── Step 6: Obtain SSL certificate ───────────────────────────────────────────
echo ""
echo "🔒 Step 6: Obtaining Let's Encrypt SSL certificate..."
echo "   Domain: ${DOMAIN}"
echo "   Email:  ${EMAIL}"
echo ""
certbot --nginx \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m "${EMAIL}" \
    --redirect
echo "   ✅ SSL certificate obtained and HTTPS redirect configured"

# ── Step 7: Reload nginx (now HTTPS) ─────────────────────────────────────────
echo ""
echo "🔄 Step 7: Final nginx reload (HTTPS active)..."
systemctl reload nginx
echo "   ✅ nginx reloaded with HTTPS"

# ── Step 8: Update .env ───────────────────────────────────────────────────────
echo ""
echo "⚙️  Step 8: Updating .env with branded domain..."

NEW_URL="https://${DOMAIN}"

if [ ! -f "${ENV_FILE}" ]; then
    echo "   ⚠️  .env not found at ${ENV_FILE} — skipping env update"
    echo "      Manually set these in your .env:"
    echo "        DASHBOARD_PUBLIC_URL=${NEW_URL}"
    echo "        BB_WEBHOOK_PUBLIC_URL=${NEW_URL}"
else
    # Update or add DASHBOARD_PUBLIC_URL
    if grep -q "^DASHBOARD_PUBLIC_URL=" "${ENV_FILE}"; then
        sed -i "s|^DASHBOARD_PUBLIC_URL=.*|DASHBOARD_PUBLIC_URL=${NEW_URL}|" "${ENV_FILE}"
        echo "   ✅ Updated DASHBOARD_PUBLIC_URL=${NEW_URL}"
    else
        echo "DASHBOARD_PUBLIC_URL=${NEW_URL}" >> "${ENV_FILE}"
        echo "   ✅ Added DASHBOARD_PUBLIC_URL=${NEW_URL}"
    fi

    # Update or add BB_WEBHOOK_PUBLIC_URL
    if grep -q "^BB_WEBHOOK_PUBLIC_URL=" "${ENV_FILE}"; then
        sed -i "s|^BB_WEBHOOK_PUBLIC_URL=.*|BB_WEBHOOK_PUBLIC_URL=${NEW_URL}|" "${ENV_FILE}"
        echo "   ✅ Updated BB_WEBHOOK_PUBLIC_URL=${NEW_URL}"
    else
        echo "BB_WEBHOOK_PUBLIC_URL=${NEW_URL}" >> "${ENV_FILE}"
        echo "   ✅ Added BB_WEBHOOK_PUBLIC_URL=${NEW_URL}"
    fi
fi

# ── Step 9: Restart dashboard container ──────────────────────────────────────
echo ""
echo "🐳 Step 9: Restarting dashboard container to pick up new .env..."
cd "${REPO_PATH}"
docker compose restart dashboard
echo "   ✅ Dashboard restarted"

# ── Verification ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:    https://${DOMAIN}"
echo "  Geo links:    https://${DOMAIN}/g/<token>"
echo "  BB webhooks:  https://${DOMAIN}/api/webhooks/bluebubbles"
echo ""
echo "  Verify with:"
echo "    curl -I https://${DOMAIN}/health"
echo "    curl -I https://${DOMAIN}/g/test-token"
echo ""
echo "  Auto-renewal is handled by certbot's systemd timer."
echo "  Check: systemctl status certbot.timer"
echo ""

# ── Certbot auto-renewal test ─────────────────────────────────────────────────
echo "🔁 Testing certbot auto-renewal (dry run)..."
certbot renew --dry-run --quiet && echo "   ✅ Auto-renewal dry run passed" || echo "   ⚠️  Auto-renewal dry run failed — check certbot logs"
