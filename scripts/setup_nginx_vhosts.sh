#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# Install every VPS nginx vhost from nginx/*.conf
# Run as root on the Hetzner VPS after git pull.
#
#   bash /opt/shamrock-leads/scripts/setup_nginx_vhosts.sh
#   bash /opt/shamrock-leads/scripts/setup_nginx_vhosts.sh --certbot edit
#
# Does NOT overwrite a dest file that already has a live Let's Encrypt path
# unless --force is passed. Safe to re-run for new hosts (edit).
# ════════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_PATH="${REPO_PATH:-/opt/shamrock-leads}"
NGINX_SRC="${REPO_PATH}/nginx"
EMAIL="admin@shamrockbailbonds.biz"
FORCE=0
CERTBOT_HOSTS=()

usage() {
  cat <<'EOF'
Usage: setup_nginx_vhosts.sh [--force] [--certbot HOST]...

  --force          Overwrite existing sites-available files
  --certbot HOST   Run certbot --nginx -d HOST after install (repeatable)
                   HOST may be a short label (edit) or FQDN
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --certbot)
      [[ $# -ge 2 ]] || { echo "missing HOST for --certbot"; exit 1; }
      CERTBOT_HOSTS+=("$2")
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ ! -d "${NGINX_SRC}" ]]; then
  echo "❌ nginx source dir missing: ${NGINX_SRC}"
  exit 1
fi

echo "════════════════════════════════════════════════════════"
echo "  Shamrock — install nginx vhosts from ${NGINX_SRC}"
echo "════════════════════════════════════════════════════════"

apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx
mkdir -p /var/www/certbot

shopt -s nullglob
for src in "${NGINX_SRC}"/*.conf; do
  name="$(basename "${src}")"
  dest="/etc/nginx/sites-available/${name}"
  if [[ -f "${dest}" && "${FORCE}" -eq 0 ]]; then
    if grep -q "letsencrypt/live" "${dest}"; then
      # HTTP-only sources must not wipe a live certbot vhost. Sources that
      # already ship ssl_certificate paths (sign/social/edit) are safe to
      # replace so origin/upstream updates (e.g. Tailscale → Docker) apply.
      if ! grep -q "ssl_certificate" "${src}"; then
        echo "   ↷ keep existing ${name} (has TLS cert paths; pass --force to replace)"
        ln -sf "${dest}" "/etc/nginx/sites-enabled/${name}"
        continue
      fi
    fi
  fi
  cp "${src}" "${dest}"
  ln -sf "${dest}" "/etc/nginx/sites-enabled/${name}"
  echo "   ✅ installed ${name}"
done

if [[ -f /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
  echo "   ✅ removed default site"
fi

echo ""
echo "🧪 nginx -t"
nginx -t
systemctl reload nginx
echo "   ✅ nginx reloaded"

fqdn_for() {
  local h="$1"
  if [[ "${h}" == *.* ]]; then
    echo "${h}"
  else
    echo "${h}.shamrockbailbonds.biz"
  fi
}

for raw in "${CERTBOT_HOSTS[@]+"${CERTBOT_HOSTS[@]}"}"; do
  host="$(fqdn_for "${raw}")"
  echo ""
  echo "🔒 certbot --nginx -d ${host}"
  certbot --nginx \
    -d "${host}" \
    --non-interactive \
    --agree-tos \
    -m "${EMAIL}" \
    --redirect
done

echo ""
echo "Done. Inventory: python3 ${REPO_PATH}/scripts/check_subdomains.py"
echo "Live probe:     python3 ${REPO_PATH}/scripts/check_subdomains.py --live"
