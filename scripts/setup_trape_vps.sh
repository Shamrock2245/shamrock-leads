#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# setup_trape_vps.sh — Trape OSINT Skip-Trace Server Setup
# Hetzner VPS: 178.156.179.237
#
# USAGE:
#   ssh root@178.156.179.237
#   bash /opt/shamrock-leads/scripts/setup_trape_vps.sh
#
# WHAT IT DOES:
#   1. Installs system dependencies (Python 3, pip, git)
#   2. Clones Trape to /opt/trape
#   3. Installs Trape Python requirements
#   4. Creates a systemd service (trape-session.service) — MANUAL start only
#   5. Installs and enables the Nginx vhost for trape.shamrockbailbonds.biz
#   6. Obtains a Let's Encrypt SSL certificate via certbot
#   7. Writes a helper script: /usr/local/bin/trape-start
#
# AFTER SETUP:
#   - To start a skip-trace session:
#       trape-start --url "https://trape.shamrockbailbonds.biz/news" \
#                   --port 8099 --accesskey <SESSION_ID>
#   - To stop:
#       systemctl stop trape-session
#   - Admin panel (local only via SSH tunnel):
#       ssh -L 8099:localhost:8099 root@178.156.179.237
#       then open: http://localhost:8099/admin
#
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

TRAPE_DIR="/opt/trape"
NGINX_AVAILABLE="/etc/nginx/sites-available/trape.shamrockbailbonds.biz.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/trape.shamrockbailbonds.biz.conf"
REPO_DIR="/opt/shamrock-leads"
DOMAIN="trape.shamrockbailbonds.biz"
ADMIN_EMAIL="admin@shamrockbailbonds.biz"

echo "════════════════════════════════════════════════════════════"
echo "  Shamrock Bail Bonds — Trape OSINT Setup"
echo "  VPS: 178.156.179.237"
echo "════════════════════════════════════════════════════════════"

# ── 1. System dependencies ────────────────────────────────────────────────────
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    git curl wget \
    nginx certbot python3-certbot-nginx \
    2>/dev/null
echo "      ✅ System deps installed"

# ── 2. Clone Trape ────────────────────────────────────────────────────────────
echo "[2/7] Cloning Trape to ${TRAPE_DIR}..."
if [ -d "${TRAPE_DIR}/.git" ]; then
    echo "      Trape already cloned — pulling latest..."
    cd "${TRAPE_DIR}" && git pull origin master 2>/dev/null || git pull origin main 2>/dev/null || true
else
    git clone --depth 1 https://github.com/jofpin/trape "${TRAPE_DIR}"
fi
echo "      ✅ Trape at ${TRAPE_DIR}"

# ── 3. Python virtual environment + requirements ──────────────────────────────
echo "[3/7] Setting up Python venv and installing Trape requirements..."
cd "${TRAPE_DIR}"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
deactivate
echo "      ✅ Trape Python environment ready at ${TRAPE_DIR}/venv"

# ── 4. Systemd service (manual-start only) ────────────────────────────────────
echo "[4/7] Writing systemd service unit (trape-session.service)..."
cat > /etc/systemd/system/trape-session.service << 'UNIT'
[Unit]
Description=Trape OSINT Skip-Trace Session
After=network.target
# NOTE: This service is NOT enabled for auto-start.
# Start manually: systemctl start trape-session
# Stop manually:  systemctl stop trape-session

[Service]
Type=simple
WorkingDirectory=/opt/trape
# Arguments are injected via /etc/trape-session.env
EnvironmentFile=-/etc/trape-session.env
ExecStart=/opt/trape/venv/bin/python3 /opt/trape/trape.py \
    --url ${TRAPE_LURE_URL} \
    --port 8099 \
    --accesskey ${TRAPE_ACCESS_KEY}
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=trape-session

[Install]
# Deliberately NOT installed — must be started manually per session
WantedBy=multi-user.target
UNIT

# Write default env file (will be overwritten by trape-start helper)
cat > /etc/trape-session.env << 'ENV'
TRAPE_LURE_URL=https://trape.shamrockbailbonds.biz/news
TRAPE_ACCESS_KEY=changeme
ENV

systemctl daemon-reload
echo "      ✅ systemd unit written (NOT auto-enabled)"

# ── 5. Nginx vhost ────────────────────────────────────────────────────────────
echo "[5/7] Installing Nginx vhost for ${DOMAIN}..."
if [ -f "${REPO_DIR}/nginx/trape.shamrockbailbonds.biz.conf" ]; then
    cp "${REPO_DIR}/nginx/trape.shamrockbailbonds.biz.conf" "${NGINX_AVAILABLE}"
else
    echo "      WARNING: Nginx config not found in repo — writing minimal config..."
    cat > "${NGINX_AVAILABLE}" << NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};
    location /admin { deny all; return 403; }
    location / {
        proxy_pass http://127.0.0.1:8099;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX
fi

ln -sf "${NGINX_AVAILABLE}" "${NGINX_ENABLED}" 2>/dev/null || true
nginx -t && systemctl reload nginx
echo "      ✅ Nginx vhost enabled"

# ── 6. SSL Certificate ────────────────────────────────────────────────────────
echo "[6/7] Obtaining Let's Encrypt SSL certificate for ${DOMAIN}..."
# Check if cert already exists
if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
    echo "      Certificate already exists — renewing if needed..."
    certbot renew --quiet --nginx 2>/dev/null || true
else
    certbot --nginx \
        -d "${DOMAIN}" \
        --non-interactive \
        --agree-tos \
        -m "${ADMIN_EMAIL}" \
        --redirect \
        2>&1 | tail -5
fi
echo "      ✅ SSL certificate active"

# ── 7. trape-start helper script ─────────────────────────────────────────────
echo "[7/7] Writing /usr/local/bin/trape-start helper..."
cat > /usr/local/bin/trape-start << 'HELPER'
#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
# trape-start — Start a Trape skip-trace session
#
# USAGE:
#   trape-start --url "https://trape.shamrockbailbonds.biz/news" \
#               --port 8099 \
#               --accesskey "SESSION_ID_FROM_OSINT_PANEL"
#
# The OSINT panel in the ShamrockLeads dashboard generates this command
# automatically. Copy-paste it into your SSH session.
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LURE_URL="https://trape.shamrockbailbonds.biz/news"
PORT="8099"
ACCESS_KEY="$(date +%s | sha256sum | head -c 16)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)       LURE_URL="$2";    shift 2 ;;
        --port)      PORT="$2";        shift 2 ;;
        --accesskey) ACCESS_KEY="$2";  shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Stop any existing session
systemctl stop trape-session 2>/dev/null || true

# Write session env
cat > /etc/trape-session.env << ENV
TRAPE_LURE_URL=${LURE_URL}
TRAPE_ACCESS_KEY=${ACCESS_KEY}
ENV

systemctl daemon-reload
systemctl start trape-session

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Trape Session ACTIVE                                    ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Lure URL:    ${LURE_URL}"
echo "║  Access Key:  ${ACCESS_KEY}"
echo "║  Port:        ${PORT}"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Admin Panel (SSH tunnel only):                          ║"
echo "║  ssh -L 8099:localhost:8099 root@178.156.179.237         ║"
echo "║  then: http://localhost:8099/admin                       ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  To stop: systemctl stop trape-session                   ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Session logs: journalctl -u trape-session -f"
HELPER

chmod +x /usr/local/bin/trape-start
echo "      ✅ trape-start helper installed at /usr/local/bin/trape-start"

# ── Final status ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Trape setup complete!"
echo ""
echo "  Trape directory:  ${TRAPE_DIR}"
echo "  Public URL:       https://${DOMAIN}"
echo "  Admin (SSH only): ssh -L 8099:localhost:8099 root@178.156.179.237"
echo ""
echo "  To start a session:"
echo "    trape-start --url 'https://${DOMAIN}/news' \\"
echo "                --port 8099 \\"
echo "                --accesskey 'YOUR_SESSION_ID'"
echo ""
echo "  Set in .env on VPS:"
echo "    TRAPE_SERVER_URL=https://${DOMAIN}"
echo "    TRAPE_DIR=${TRAPE_DIR}"
echo "════════════════════════════════════════════════════════════"
